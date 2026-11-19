"""UUID -> Minecraft username, stored in the players table.

The table is the store, not a cache in front of one: a uuid resolved once is
kept indefinitely and refreshed on a schedule. That split is deliberate and is
the whole point of this module's entry points.

`lookup_names` is the read path. It only ever SELECTs, so a request can never
block on the upstream API or hold a pooled connection across a network call.
A uuid with no row yet simply has no name in the response.

`resolve_names` and `revalidate_names` are the write paths and belong to the
worker (see app/services/player_sync.py). They call upstream and persist.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow as _utcnow
from app.repositories.player_repo import PlayerRepository
from app.services import mojang

logger = logging.getLogger(__name__)

# How old a stored name may get before the worker queues it for a refresh.
# Nothing expires on read -- a name past this age is still served.
REFRESH_AFTER = timedelta(days=25)

# How long a failed lookup sits out of the refresh queues. Without it the same
# unresolvable uuids head both queues every cycle and starve everything behind
# them -- which froze the players table from 2026-10-02 to 2026-11-19.
RETRY_FAILED_AFTER = timedelta(hours=6)


def _by_clean(uuids: list[str]) -> dict[str, str]:
    """Dash-stripped uuid -> the form the caller passed, which keys the result."""
    return {uuid.replace("-", ""): uuid for uuid in uuids if uuid}


async def lookup_names(db: AsyncSession, uuids: list[str]) -> dict[str, str]:
    """Stored names, whatever their age. Never calls upstream, never writes."""
    by_clean = _by_clean(uuids)
    if not by_clean:
        return {}

    players = await PlayerRepository(db).by_uuid(list(by_clean))
    return {
        original: players[clean].username
        for clean, original in by_clean.items()
        if clean in players
    }


async def resolve_names(db: AsyncSession, uuids: list[str]) -> dict[str, str]:
    """Fetch and persist names for uuids that are missing or due a refresh.

    One request per uuid -- the only option when there is no stored name to
    revalidate. Worker-only: this does network I/O and commits.
    """
    by_clean = _by_clean(uuids)
    if not by_clean:
        return {}

    repo = PlayerRepository(db)
    players = await repo.by_uuid(list(by_clean))

    names: dict[str, str] = {}
    to_fetch = []
    now = _utcnow()

    for clean, original in by_clean.items():
        player = players.get(clean)
        if player and now - player.last_updated < REFRESH_AFTER:
            names[original] = player.username
        else:
            to_fetch.append((clean, original, player))

    if not to_fetch:
        return names

    fetched = await asyncio.gather(*[mojang.name_for_uuid(clean) for clean, _, _ in to_fetch])

    new_players, failed = [], []
    for (clean, original, player), username in zip(to_fetch, fetched):
        if username:
            new_players.append({"uuid": clean, "username": username, "last_updated": now})
            names[original] = username
        else:
            failed.append(clean)
            if player:
                names[original] = player.username

    if new_players:
        await repo.upsert(new_players)
        await repo.clear_failures([row["uuid"] for row in new_players])
    await repo.record_failures(failed, now)
    await db.commit()

    return names


async def revalidate_names(db: AsyncSession, players: list[tuple[str, str]]) -> tuple[int, int]:
    """Refresh stored names ten at a time. Returns (confirmed, rechecked).

    Proving a stored name still belongs to the uuid we hold it against costs a
    tenth of a request, because the bulk endpoint runs name->uuid and we
    already have the name. Only names that moved or vanished -- roughly one in
    five, and the only ones whose stored value is actually wrong -- fall
    through to a per-uuid lookup.

    Confirmed rows are touched, not rewritten: the name is unchanged, so only
    last_updated moves.
    """
    if not players:
        return 0, 0

    live = await mojang.uuids_for_names([name for _, name in players])

    confirmed, changed = [], []
    for uuid, name in players:
        # Absent means the name is unregistered; a different uuid means it was
        # taken over. Either way the stored name is no longer this player's.
        (confirmed if live.get(name.lower()) == uuid else changed).append(uuid)

    if confirmed:
        await PlayerRepository(db).touch(confirmed, _utcnow())
        await db.commit()

    if changed:
        await resolve_names(db, changed)

    return len(confirmed), len(changed)
