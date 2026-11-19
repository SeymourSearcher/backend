"""Which auction caches a sync drops, which it leaves to their TTL, and which
the worker keeps warm. See app/services/auction_cache.py.

None of this is visible from the endpoint: a poll that inserts nothing must not
touch the caches, a new sale must not throw away the stats, and the warm-up must
hit the exact keys the pages read.
"""
import pytest

from app.clock import utcnow
from app.models import ActiveAuction, EndedAuction
from app.services import auction_cache, auction_stats, auction_sync
from app.services.auctions import SeymourEndedAuction
from app.services.auction_queries import AuctionFilters, filters_key
from app.services.auction_stats import compute_auction_stats
from app.services.cache import AUCTION_COUNT, AUCTION_STATS, TOP_SALES
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


@pytest.mark.parametrize("filters", [DEFAULT, AuctionFilters(tier="t1", include_dyes=True)])
async def test_a_new_sale_leaves_the_stats_to_their_ttl(seeded, redis, filters):
    """One sale in ~250k cannot visibly move an aggregate, and dropping them on
    every sale meant the TTL never applied."""
    before = await prime_stats(seeded, redis, filters)

    seeded.add(ended(1000))
    await seeded.commit()
    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))

    cached = await AUCTION_STATS.get(redis, filters_key(EndedAuction, filters))
    assert cached is not None
    assert cached["total"] == before["total"]


async def test_a_cold_entry_is_not_computed_just_because_rows_changed(seeded, redis):
    key = filters_key(EndedAuction, DEFAULT)
    assert await AUCTION_STATS.get(redis, key) is None

    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))

    assert await AUCTION_STATS.get(redis, key) is None


async def test_a_new_sale_drops_the_row_level_entries(seeded, redis):
    """The ticker and the row-count label describe rows a visitor can see."""
    ended_key = filters_key(EndedAuction, AuctionFilters(tier="t1"))
    await AUCTION_COUNT.set(redis, 7, ended_key)
    await TOP_SALES.set(redis, [], 24, 20, True)

    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))

    assert await AUCTION_COUNT.get(redis, ended_key) is None
    assert await TOP_SALES.get(redis, 24, 20, True) is None


async def test_warming_fills_every_page_entry_once(seeded, redis):
    assert await auction_cache.warm_stats(seeded, redis) == len(auction_cache.WARM_STATS)
    assert await auction_cache.warm_stats(seeded, redis) == 0


async def test_an_entry_near_expiry_is_rebuilt_with_the_new_rows(seeded, redis):
    await auction_cache.warm_stats(seeded, redis)
    seeded.add(ended(999))
    await seeded.commit()

    key = filters_key(EndedAuction, DEFAULT)
    await redis.expire(AUCTION_STATS.key(key), auction_cache.REWARM_MARGIN_SECONDS - 1)

    assert await auction_cache.warm_stats(seeded, redis) == 1
    assert (await AUCTION_STATS.get(redis, key))["total"] == 31


@pytest.mark.parametrize("query", ["", "?include_dyes=false", "?pieces=0", "?pieces=3"])
async def test_the_warmed_keys_are_the_ones_the_pages_read(client, db, redis, monkeypatch, query):
    """WARM_STATS has to equal what the router builds from these query strings.
    If it drifts, warming still 'works' -- against keys nobody reads."""
    db.add_all([ended(i) for i in range(30)])
    await db.commit()
    await auction_cache.warm_stats(db, redis)

    async def no_recompute(*a):
        pytest.fail("a warmed page recomputed its stats")

    monkeypatch.setattr(auction_stats, "_compute", no_recompute)

    assert (await client.get(f"/auctions/stats{query}")).status_code == 200


async def test_a_warm_up_failure_does_not_propagate(seeded, monkeypatch):
    async def boom(*a):
        raise RuntimeError("redis down")

    monkeypatch.setattr(auction_sync, "get_redis", boom)
    await auction_sync._warm_stats(seeded)


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
    async def payload(client):
        return {"auctions": [{"auction_id": "e00000"}]}

    monkeypatch.setattr(auction_sync, "get_auctions_ended", payload)
    monkeypatch.setattr(auction_sync, "parse_ended_auction", lambda raw: ended_payload(0))

    db.add(ActiveAuction(
        auction_id="e00000", seller_uuid="s", price=1, is_bin=True, start_time=0,
        end_time=None, item_uuid="i", hex_color="FF0000", piece=0, last_updated=utcnow(),
    ))
    await db.commit()

    changes = await auction_sync.sync_ended_auctions(db, None)
    await db.commit()

    assert changes.ended_inserted == 1
    assert changes.active_changed == 1, "the ended auction should have left active_auctions"

    # The same payload again: Hypixel has not refreshed, so nothing changed.
    assert await auction_sync.sync_ended_auctions(db, None) == auction_sync.SyncChanges()


async def test_a_hot_count_is_rebuilt_under_the_key_the_default_request_reads(client, db, redis):
    db.add_all([ended(i) for i in range(30)])
    await db.commit()

    assert (await client.get("/auctions/ended")).json()["total"] == 30

    db.add(ended(999))
    await db.commit()
    await auction_sync._refresh_caches(db, auction_sync.SyncChanges(ended_inserted=1))

    assert await AUCTION_COUNT.get(redis, filters_key(EndedAuction, DEFAULT)) == 31
    assert (await client.get("/auctions/ended")).json()["total"] == 31


async def test_a_refresh_failure_does_not_propagate(seeded, monkeypatch):
    """A cache problem must not fail the sync that wrote the rows."""
    async def boom(*a):
        raise RuntimeError("redis down")

    monkeypatch.setattr(auction_sync, "get_redis", boom)
    await auction_sync._refresh_caches(seeded, auction_sync.SyncChanges(ended_inserted=1))
