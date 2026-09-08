"""The whole seymours table, cached once in the shape the hex page walks.

Every hex lookup needs the same rows -- the query does not depend on the hex
code -- so a cold lookup was paying ~1s to rebuild an identical payload, which
was 97% of a 2.7s request. Cached instead, in a normalised shape: each user
once, then two flat arrays holding the per-row user index and hex colour.

Measured on prod data (106,279 rows, 32 users): the query is 1034ms; this
payload is 0.85MB, encodes in 4ms, decodes in 1ms, and Redis serves it in
0.3ms. Naive JSON of the raw rows also beats the query comfortably (8.9MB,
41ms decode) -- the normalised shape wins on top of that because it hoists the
per-row int(hex, 16) parse to write time, which makes the matching pass itself
faster (23ms against 37ms). The measurements predate the pieces column, which
adds one byte per row.

The blob shape is versioned by the cache key (see OWNER_HEXES in
app/services/cache.py) -- _decode has no tolerance for a payload written by an
older revision.
"""
import asyncio
from array import array
from base64 import b64decode, b64encode
from typing import NamedTuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.seymour_repo import SeymourRepository
from app.services.cache import OWNER_HEXES

# Both columns are packed with the same typecode so there is no per-column width
# to get wrong; 'I' also leaves no user-count cliff to trip over later.
TYPECODE = "I"


PIECE_TYPECODE = "B"


class OwnerHexes(NamedTuple):
    """users[idx[i]] owns hexes[i] on piece pieces[i], for every Seymour."""

    users: list[list]  # [user_id, username, discord_id, avatar_hash]
    idx: array
    hexes: array
    pieces: array


def _build(rows: list[tuple[int, str, int, str | None, str, int]]) -> OwnerHexes:
    users: list[list] = []
    index_of: dict[int, int] = {}
    idx, hexes = array(TYPECODE), array(TYPECODE)
    pieces = array(PIECE_TYPECODE)

    for user_id, username, discord_id, avatar_hash, hex_color, item_name in rows:
        i = index_of.get(user_id)
        if i is None:
            i = index_of[user_id] = len(users)
            users.append([user_id, username, discord_id, avatar_hash])
        idx.append(i)
        hexes.append(int(hex_color, 16))
        pieces.append(item_name)

    return OwnerHexes(users, idx, hexes, pieces)


def _encode(owners: OwnerHexes) -> dict:
    return {
        "users": owners.users,
        "idx": b64encode(owners.idx.tobytes()).decode(),
        "hexes": b64encode(owners.hexes.tobytes()).decode(),
        "pieces": b64encode(owners.pieces.tobytes()).decode(),
    }


def _decode(payload: dict) -> OwnerHexes:
    # array.tobytes() is native-endian, so a blob written on one architecture
    # would decode to garbage on another. Fine while one host writes it.
    idx, hexes = array(TYPECODE), array(TYPECODE)
    pieces = array(PIECE_TYPECODE)
    idx.frombytes(b64decode(payload["idx"]))
    hexes.frombytes(b64decode(payload["hexes"]))
    pieces.frombytes(b64decode(payload["pieces"]))
    return OwnerHexes(payload["users"], idx, hexes, pieces)


_rebuild = asyncio.Lock()


async def _query_and_store(db: AsyncSession, redis: Redis) -> OwnerHexes:
    owners = _build(await SeymourRepository(db).list_owner_hexes())
    await OWNER_HEXES.set(redis, _encode(owners))
    return owners


async def refresh_owner_hexes(db: AsyncSession, redis: Redis) -> None:
    """Rebuild the blob now, so no reader has to.

    Every write to the seymours table calls this. The writer pays the ~600ms
    query that the next hex visitor would otherwise have paid, and an upload is
    not latency-sensitive the way a page view is. This is what lets OWNER_HEXES
    carry a backstop TTL instead of a 30-minute one -- see app/services/cache.py.
    """
    async with _rebuild:
        await _query_and_store(db, redis)


async def get_owner_hexes(db: AsyncSession, redis: Redis) -> OwnerHexes:
    """One rebuild at a time, because a hex page view is four requests at once.

    All four call this, so a cold cache used to mean four concurrent copies of
    the same 106k-row query -- measured at 556ms each, and they compete for the
    connection pool while they run. The lock is per worker, so N workers can
    still rebuild N times; that is 4x better than N*4 and needs no cross-process
    coordination.
    """
    cached = await OWNER_HEXES.get(redis)
    if cached is not None:
        return _decode(cached)

    async with _rebuild:
        # Re-read inside the lock: whoever queued here wants the winner's
        # result, not another identical query.
        cached = await OWNER_HEXES.get(redis)
        if cached is not None:
            return _decode(cached)

        return await _query_and_store(db, redis)
