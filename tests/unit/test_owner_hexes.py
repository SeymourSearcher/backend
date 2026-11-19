"""The snapshot is packed binary inside JSON, so a round-trip through the cache
has to come back byte-identical or the matching pass silently scores wrong users.
"""
import asyncio
import json

import fakeredis.aioredis
import pytest

from app.services import owner_hexes
from app.services.owner_hexes import (
    OwnerHexes,
    _build,
    _decode,
    _encode,
    get_owner_hexes,
    refresh_owner_hexes,
)

ROWS = [
    (7, "exact", 11, None, "FFFF00", 0),
    (7, "exact", 11, None, "000000", 3),
    (9, "close", 12, "abc", "FFFE00", 1),
]


def round_trip(rows) -> OwnerHexes:
    """Through json, exactly as CacheEntry.set/get would."""
    return _decode(json.loads(json.dumps(_encode(_build(rows)))))


def test_each_user_is_stored_once_and_rows_point_at_them():
    owners = _build(ROWS)

    assert owners.users == [[7, "exact", 11, None], [9, "close", 12, "abc"]]
    assert list(owners.idx) == [0, 0, 1]


def test_hex_colours_are_stored_as_ints():
    assert list(_build(ROWS).hexes) == [0xFFFF00, 0x000000, 0xFFFE00]


def test_pieces_stay_aligned_with_their_rows():
    assert list(_build(ROWS).pieces) == [0, 3, 1]


def test_the_round_trip_preserves_everything():
    original, restored = _build(ROWS), round_trip(ROWS)

    assert restored.users == original.users
    assert list(restored.idx) == list(original.idx)
    assert list(restored.hexes) == list(original.hexes)
    assert list(restored.pieces) == list(original.pieces)


def test_an_empty_table_round_trips():
    owners = round_trip([])

    assert owners.users == []
    assert list(owners.idx) == []
    assert list(owners.hexes) == []
    assert list(owners.pieces) == []


def test_lowercase_hex_survives_as_the_same_colour():
    """The column is not case-normalised, but the int and the rebuilt string are."""
    owners = round_trip([(7, "exact", 11, None, "ffff00", 0)])

    assert list(owners.hexes) == [0xFFFF00]
    assert f"{owners.hexes[0]:06X}" == "FFFF00"


@pytest.fixture
def counting_repo(monkeypatch):
    calls = []

    class Repo:
        def __init__(self, session):
            pass

        async def list_owner_hexes(self):
            calls.append(1)
            # Long enough that the other callers are definitely queued behind
            # this one rather than finishing first by luck.
            await asyncio.sleep(0.05)
            return ROWS

    monkeypatch.setattr(owner_hexes, "SeymourRepository", Repo)
    return calls


class TestSingleFlight:
    """A hex page view is four requests firing together, and all four land here.

    Without the lock a cold cache meant four copies of the same whole-table
    query: measured at 556ms each over 106k rows, 2.4s for the page against
    0.6s sequential. See tests/bench/bench_endpoints.py.
    """

    async def test_concurrent_callers_share_one_rebuild(self, counting_repo):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        results = await asyncio.gather(*(get_owner_hexes(None, redis) for _ in range(4)))

        assert len(counting_repo) == 1
        assert all(list(r.hexes) == [0xFFFF00, 0x000000, 0xFFFE00] for r in results)
        await redis.aclose()

    async def test_a_later_caller_reads_the_cache_rather_than_the_lock(self, counting_repo):
        """The queued callers must not each re-run the query once they wake."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        await get_owner_hexes(None, redis)
        await get_owner_hexes(None, redis)

        assert len(counting_repo) == 1
        await redis.aclose()


class TestRefreshOnWrite:
    """Writers rebuild the blob so readers never meet an empty key."""

    async def test_a_refresh_leaves_the_cache_warm(self, counting_repo):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        await refresh_owner_hexes(None, redis)
        owners = await get_owner_hexes(None, redis)

        assert len(counting_repo) == 1
        assert list(owners.hexes) == [0xFFFF00, 0x000000, 0xFFFE00]
        await redis.aclose()

    async def test_a_refresh_requeries_over_a_live_key(self, counting_repo):
        """Unlike a read, it must not trust what is already there -- the rows it
        is called for are exactly the ones that just changed."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        await get_owner_hexes(None, redis)
        await refresh_owner_hexes(None, redis)

        assert len(counting_repo) == 2
        await redis.aclose()


class TestDecodedSnapshot:
    """Warm reads reuse one decoded snapshot until the blob changes."""

    async def test_an_unchanged_blob_is_decoded_once(self, counting_repo):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        first = await get_owner_hexes(None, redis)
        second = await get_owner_hexes(None, redis)
        third = await get_owner_hexes(None, redis)

        assert second is third
        assert list(second.hexes) == list(first.hexes)
        await redis.aclose()

    async def test_a_new_blob_is_not_served_from_the_old_decode(self, counting_repo, monkeypatch):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        before = await get_owner_hexes(None, redis)
        await get_owner_hexes(None, redis)

        monkeypatch.setattr(owner_hexes.SeymourRepository, "list_owner_hexes", _one_row)
        await refresh_owner_hexes(None, redis)
        after = await get_owner_hexes(None, redis)

        assert len(before.hexes) == 3
        assert list(after.hexes) == [0x123456]
        assert after.labs.tolist() == owner_hexes.ALL_LAB[[0x123456]].tolist()
        await redis.aclose()


async def _one_row(self):
    return [(1, "solo", 10, None, "123456", 2)]
