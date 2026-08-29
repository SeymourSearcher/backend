"""The auction caches are now dropped by the sync rather than by expiry.

Two things make that safe, and neither is visible from the endpoint: a poll that
inserts nothing must not touch the caches at all, and a poll that does insert
must not rebuild an entry nobody is reading. Both are what stop the rewrite from
turning a per-request cost into a permanent background one.
"""
import pytest

from app.clock import utcnow
from app.models import ActiveAuction, EndedAuction
from app.services import auction_cache, auction_sync
from app.services.auctions import SeymourEndedAuction
from app.services.auction_queries import AuctionFilters, filters_key
from app.services.auction_stats import compute_auction_stats
from app.services.cache import AUCTION_COUNT, AUCTION_STATS
from app.services.matching import get_tier

pytestmark = pytest.mark.integration

BASE_MS = 1_700_000_000_000
DEFAULT = AuctionFilters(include_dyes=True)


def ended(i: int) -> EndedAuction:
    delta_e = (i % 20) * 0.7
    return EndedAuction(
        auction_id=f"e{i:05d}", seller_uuid="s", buyer_uuid="b",
        price=100 + (i * 137) % 9_000, is_bin=(i % 3 == 0), item_uuid=f"i{i}",
        hex_color="FF0000", piece=i % 4, timestamp=BASE_MS + i * 3_600_000,
        target_hex_keys="FF0102", delta_e_keys=delta_e, tier_keys=get_tier(delta_e),
        target_hex_all="FF0000", delta_e_all=delta_e, tier_all=get_tier(delta_e),
    )


@pytest.fixture
async def seeded(db):
    db.add_all([ended(i) for i in range(30)])
    await db.commit()
    return db


@pytest.fixture(autouse=True)
def worker_redis(redis, monkeypatch):
    """The sync loop resolves its own client rather than taking one from FastAPI."""
    async def _get_redis():
        return redis

    monkeypatch.setattr(auction_sync, "get_redis", _get_redis)


async def prime_stats(db, redis, filters=DEFAULT):
    """Read once, the way a visitor would, so the entry is live."""
    return await compute_auction_stats(db, filters, redis=redis)


async def test_a_poll_that_inserts_nothing_leaves_the_caches_alone(seeded, redis, monkeypatch):
    before = await prime_stats(seeded, redis)
    monkeypatch.setattr(
        auction_cache, "refresh_ended",
        lambda *a: pytest.fail("refreshed on a poll that changed nothing"),
    )

    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges())

    assert await AUCTION_STATS.get(redis, filters_key(EndedAuction, DEFAULT)) is not None
    assert (await prime_stats(seeded, redis))["total"] == before["total"]


async def test_a_hot_entry_is_rebuilt_with_the_new_rows(seeded, redis):
    await prime_stats(seeded, redis)

    seeded.add(ended(999))
    await seeded.commit()
    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))

    cached = await AUCTION_STATS.get(redis, filters_key(EndedAuction, DEFAULT))
    assert cached is not None, "a live entry should have been rebuilt, not just dropped"
    assert cached["total"] == 31


async def test_a_cold_entry_is_not_computed_just_because_rows_changed(seeded, redis):
    """The whole point of using presence as the hotness signal: an idle site
    must not pay the ~800ms scan every time an auction ends."""
    key = filters_key(EndedAuction, DEFAULT)
    assert await AUCTION_STATS.get(redis, key) is None

    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))

    assert await AUCTION_STATS.get(redis, key) is None


async def test_a_stale_entry_is_dropped_even_when_it_is_not_rebuilt(seeded, redis):
    """Non-default filters are never rebuilt, so they have to be invalidated."""
    narrow = AuctionFilters(tier="t1", include_dyes=True)
    await prime_stats(seeded, redis, narrow)
    key = filters_key(EndedAuction, narrow)
    assert await AUCTION_STATS.get(redis, key) is not None

    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))

    assert await AUCTION_STATS.get(redis, key) is None


async def test_ended_changes_do_not_disturb_the_active_count(seeded, redis):
    active_key = filters_key(ActiveAuction, DEFAULT)
    await AUCTION_COUNT.set(redis, 7, active_key)

    await auction_cache.refresh_ended(seeded, redis)

    assert await AUCTION_COUNT.get(redis, active_key) == 7


async def test_active_changes_do_not_disturb_the_ended_stats(seeded, redis):
    await prime_stats(seeded, redis)

    await auction_cache.refresh_active(seeded, redis)

    assert await AUCTION_STATS.get(redis, filters_key(EndedAuction, DEFAULT)) is not None


def ended_payload(i: int) -> SeymourEndedAuction:
    return SeymourEndedAuction(
        auction_id=f"e{i:05d}", seller="s", buyer="b", price=100, is_bin=False,
        item_uuid=f"i{i}", hex_color="FF0000", piece=0, timestamp=BASE_MS,
    )


async def test_re_upserting_the_same_rows_reports_nothing_new(db):
    """Hypixel republishes each ended auction for ~60s and we poll twice, so
    this is the common case. rowcount says 5 both times; only the id check
    distinguishes them."""
    batch = [ended_payload(i) for i in range(5)]

    assert await auction_sync.upsert_ended_auctions_batch(db, batch) == 5
    await db.commit()
    assert await auction_sync.upsert_ended_auctions_batch(db, batch) == 0
    await db.commit()


async def test_a_partly_seen_batch_counts_only_the_new_rows(db):
    await auction_sync.upsert_ended_auctions_batch(db, [ended_payload(i) for i in range(3)])
    await db.commit()

    overlapping = [ended_payload(i) for i in range(1, 6)]  # 2 seen, 3 new
    assert await auction_sync.upsert_ended_auctions_batch(db, overlapping) == 3


async def test_the_ended_sync_reports_what_it_changed(db, monkeypatch):
    async def payload():
        return {"auctions": [{"auction_id": "e00000"}]}

    monkeypatch.setattr(auction_sync, "get_auctions_ended", payload)
    monkeypatch.setattr(auction_sync, "parse_ended_auction", lambda raw: ended_payload(0))

    db.add(ActiveAuction(
        auction_id="e00000", seller_uuid="s", price=1, is_bin=True, start_time=0,
        end_time=None, item_uuid="i", hex_color="FF0000", piece=0, last_updated=utcnow(),
    ))
    await db.commit()

    changes = await auction_sync.sync_ended_auctions(db)
    await db.commit()

    assert changes.ended_inserted == 1
    assert changes.active_changed == 1, "the ended auction should have left active_auctions"

    # The same payload again: Hypixel has not refreshed, so nothing changed.
    assert await auction_sync.sync_ended_auctions(db) == auction_sync.SyncChanges()


async def test_the_warmed_key_is_the_one_the_default_request_reads(client, db, redis):
    """DEFAULT_FILTERS has to equal what the router builds from an empty query
    string. If it drifts, warming still 'works' -- against a key nobody reads."""
    db.add_all([ended(i) for i in range(30)])
    await db.commit()

    assert (await client.get("/auctions/stats")).json()["total"] == 30

    db.add(ended(999))
    await db.commit()
    await auction_sync._refresh_caches(db, auction_sync.SyncChanges(ended_inserted=1))

    # Served straight from the entry the sync rebuilt, without recomputing.
    assert await AUCTION_STATS.exists(redis, filters_key(EndedAuction, DEFAULT))
    assert (await client.get("/auctions/stats")).json()["total"] == 31


async def test_a_refresh_failure_does_not_propagate(seeded, monkeypatch):
    """A cache problem must not fail the sync that wrote the rows."""
    async def boom(*a):
        raise RuntimeError("redis down")

    monkeypatch.setattr(auction_sync, "get_redis", boom)
    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))
