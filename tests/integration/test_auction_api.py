"""Auction listing endpoints.

Filtering, sorting and pagination all moved into SQL, and `total` became a real
COUNT(*) instead of the length of a truncated Python list. The pagination test
is the one that matters most: OFFSET over a non-unique sort key silently repeats
and skips rows unless the query carries a tiebreaker.
"""
import pytest

from app.clock import utcnow_ms
from app.models import ActiveAuction, EndedAuction
from app.services.matching import get_tier

pytestmark = pytest.mark.integration

BASE_MS = 1_700_000_000_000


def ended_rows(count, *, price=None):
    rows = []
    for i in range(count):
        delta_e = (i % 20) * 0.7
        rows.append(EndedAuction(
            auction_id=f"e{i:05d}", seller_uuid=f"s{i:03d}", buyer_uuid=f"b{i:03d}",
            price=price if price is not None else 100 + (i * 137) % 9_000,
            is_bin=(i % 3 == 0), item_uuid=f"i{i}", hex_color="FF0000", piece=i % 4,
            timestamp=BASE_MS + i * 1000,
            target_hex_keys="FF0102", delta_e_keys=delta_e, tier_keys=get_tier(delta_e),
            target_hex_all="FF0000", delta_e_all=delta_e, tier_all=get_tier(delta_e),
        ))
    return rows


@pytest.fixture
async def seed(db):
    async def _seed(rows):
        db.add_all(rows)
        await db.commit()
        return rows
    return _seed


class TestTopSales:
    """The homepage ticker. A rolling window, so the rows have to be stamped
    against the real clock rather than the fixed BASE_MS the other tests use."""

    @staticmethod
    def recent(price, *, hours_ago, auction_id):
        return EndedAuction(
            auction_id=auction_id, seller_uuid="s", buyer_uuid="b",
            price=price, is_bin=True, item_uuid=auction_id,
            hex_color="FF0000", piece=0,
            timestamp=utcnow_ms() - int(hours_ago * 3_600_000),
            target_hex_keys="FF0000", delta_e_keys=0.0, tier_keys="t1",
            target_hex_all="FF0000", delta_e_all=0.0, tier_all="t1",
        )

    async def test_the_dearest_sale_leads(self, client, seed):
        await seed([
            self.recent(500, hours_ago=1, auction_id="cheap"),
            self.recent(9_000, hours_ago=2, auction_id="dear"),
            self.recent(3_000, hours_ago=3, auction_id="mid"),
        ])
        prices = [s["price"] for s in (await client.get("/auctions/top-sales")).json()]

        assert prices == [9_000, 3_000, 500]

    async def test_a_sale_older_than_the_window_is_left_out(self, client, seed):
        """Price alone would put it first; the window is what keeps it out."""
        await seed([
            self.recent(100, hours_ago=1, auction_id="today"),
            self.recent(99_999, hours_ago=30, auction_id="yesterday"),
        ])
        body = (await client.get("/auctions/top-sales")).json()

        assert [s["auction_id"] for s in body] == ["today"]

    async def test_widening_the_window_lets_it_back_in(self, client, seed):
        await seed([
            self.recent(100, hours_ago=1, auction_id="today"),
            self.recent(99_999, hours_ago=30, auction_id="yesterday"),
        ])
        body = (await client.get("/auctions/top-sales", params={"hours": 48})).json()

        assert [s["auction_id"] for s in body] == ["yesterday", "today"]

    async def test_the_limit_truncates_from_the_top(self, client, seed):
        await seed([self.recent(100 * i, hours_ago=1, auction_id=f"t{i}") for i in range(1, 6)])
        body = (await client.get("/auctions/top-sales", params={"limit": 2})).json()

        assert [s["price"] for s in body] == [500, 400]

    async def test_an_empty_table_is_an_empty_list_not_an_error(self, client):
        response = await client.get("/auctions/top-sales")

        assert response.status_code == 200
        assert response.json() == []


async def test_total_is_the_full_count_not_the_page(client, seed):
    await seed(ended_rows(120))
    body = (await client.get("/auctions/ended", params={"limit": 10})).json()

    assert body["total"] == 120
    assert len(body["auctions"]) == 10


async def test_pagination_covers_every_row_exactly_once(client, seed):
    """All 60 rows share a price, so ORDER BY price alone is non-deterministic."""
    await seed(ended_rows(60, price=500))

    seen = []
    for skip in range(0, 60, 10):
        page = (await client.get("/auctions/ended", params={
            "limit": 10, "skip": skip, "sort_by": "price",
        })).json()["auctions"]
        seen.extend(a["auction_id"] for a in page)

    assert len(seen) == 60
    assert len(set(seen)) == 60


async def test_sort_order_is_applied(client, seed):
    await seed(ended_rows(30))

    ascending = (await client.get("/auctions/ended", params={
        "limit": 30, "sort_by": "price", "sort_order": "asc"})).json()["auctions"]
    descending = (await client.get("/auctions/ended", params={
        "limit": 30, "sort_by": "price", "sort_order": "desc"})).json()["auctions"]

    prices = [a["price"] for a in ascending]
    assert prices == sorted(prices)
    assert [a["price"] for a in descending] == sorted(prices, reverse=True)


@pytest.mark.parametrize("params, predicate", [
    ({"tier": "t1"}, lambda r: r.tier_all == "t1"),
    ({"pieces": "0,2"}, lambda r: r.piece in (0, 2)),
    ({"min_price": 5000}, lambda r: r.price >= 5000),
    ({"max_price": 2000}, lambda r: r.price <= 2000),
    ({"bin_filter": "bin"}, lambda r: r.is_bin),
    ({"bin_filter": "nobin"}, lambda r: not r.is_bin),
    ({"hex_color": "ff0000"}, lambda r: True),
    ({"hex_color": "00FF00"}, lambda r: False),
])
async def test_filters_match_the_expected_rows(client, seed, params, predicate):
    rows = await seed(ended_rows(120))
    body = (await client.get("/auctions/ended", params={**params, "limit": 1})).json()

    assert body["total"] == sum(1 for r in rows if predicate(r))


async def test_filters_compose(client, seed):
    rows = await seed(ended_rows(120))
    body = (await client.get("/auctions/ended", params={
        "tier": "t2", "pieces": "1,3", "min_price": 1000, "limit": 1,
    })).json()

    assert body["total"] == sum(
        1 for r in rows if r.tier_all == "t2" and r.piece in (1, 3) and r.price >= 1000
    )


async def test_include_dyes_switches_which_tier_column_filters(client, seed):
    rows = ended_rows(30)
    for row in rows:
        row.tier_keys, row.delta_e_keys = "t3", 7.0
        row.tier_all, row.delta_e_all = "t1", 1.0
    await seed(rows)

    with_dyes = (await client.get("/auctions/ended", params={
        "tier": "t1", "include_dyes": "true", "limit": 1})).json()
    without = (await client.get("/auctions/ended", params={
        "tier": "t1", "include_dyes": "false", "limit": 1})).json()

    assert with_dyes["total"] == 30
    assert without["total"] == 0


async def test_serialised_row_carries_the_derived_match_fields(client, seed):
    await seed(ended_rows(1))
    auction = (await client.get("/auctions/ended", params={"limit": 1})).json()["auctions"][0]

    assert auction["target_hex"] == "FF0000"
    assert auction["delta_e"] == 0.0
    assert auction["tier"] == "t1"
    assert auction["abs_diff"] == 0
    assert auction["seller_name"] == "player-s000"
    assert auction["buyer_name"] == "player-b000"


async def test_unmatched_rows_serialise_without_error(client, seed):
    rows = ended_rows(3)
    for row in rows:
        row.target_hex_keys = row.target_hex_all = None
        row.delta_e_keys = row.delta_e_all = None
        row.tier_keys = row.tier_all = None
    await seed(rows)

    body = (await client.get("/auctions/ended", params={"limit": 10})).json()
    assert body["total"] == 3
    assert all(a["target_hex"] == "" and a["tier"] is None for a in body["auctions"])


async def test_active_auctions_sort_by_end_time(client, seed):
    await seed([
        ActiveAuction(auction_id=f"x{i}", seller_uuid=f"s{i}", price=100, is_bin=True,
                      start_time=BASE_MS, end_time=BASE_MS + (10 - i) * 1000,
                      item_uuid=f"i{i}", hex_color="FF0000", piece=0,
                      target_hex_all="FF0000", delta_e_all=0.0, tier_all="t1")
        for i in range(5)
    ])

    body = (await client.get("/auctions/active", params={
        "limit": 10, "sort_by": "timeleft", "sort_order": "asc"})).json()

    end_times = [a["end_time"] for a in body["auctions"]]
    assert end_times == sorted(end_times)
    assert body["total"] == 5


async def test_missing_auction_is_404(client, clean_db):
    assert (await client.get("/auctions/ended/nope")).status_code == 404
    assert (await client.get("/auctions/active/nope")).status_code == 404


async def test_invalid_query_values_are_rejected(client, clean_db):
    assert (await client.get("/auctions/ended", params={"tier": "t9"})).status_code == 422
    assert (await client.get("/auctions/ended", params={"limit": 0})).status_code == 422
    assert (await client.get("/auctions/ended", params={"skip": -1})).status_code == 422


class TestCountCaching:
    """`total` is cached because it only feeds a label and costs a full scan.

    What matters is that a changed filter never reads another filter's count,
    and that a hit genuinely avoids the query.
    """

    async def test_the_count_is_served_from_cache_on_a_second_call(
        self, client, seed, redis, stub_names
    ):
        await seed(ended_rows(30))

        first = await client.get("/auctions/ended?limit=5")
        assert first.json()["total"] == 30

        keys = await redis.keys("auction_count:*")
        assert len(keys) == 1, keys

        # Rows added behind the cache must not change the answer within the TTL.
        await seed([EndedAuction(
            auction_id="extra", seller_uuid="s", buyer_uuid="b", price=1, is_bin=True,
            item_uuid="i", hex_color="FF0000", piece=0, timestamp=BASE_MS,
            target_hex_keys="FF0102", delta_e_keys=0.0, tier_keys=get_tier(0.0),
            target_hex_all="FF0000", delta_e_all=0.0, tier_all=get_tier(0.0),
        )])

        second = await client.get("/auctions/ended?limit=5")
        assert second.json()["total"] == 30
        assert len(second.json()["auctions"]) == 5

    async def test_a_different_filter_gets_its_own_count(self, client, seed, redis, stub_names):
        await seed(ended_rows(30))

        everything = (await client.get("/auctions/ended?limit=5")).json()["total"]
        one_piece = (await client.get("/auctions/ended?limit=5&pieces=0")).json()["total"]

        assert everything == 30
        assert one_piece < everything
        assert len(await redis.keys("auction_count:*")) == 2

    async def test_sort_order_shares_one_cached_count(self, client, seed, redis, stub_names):
        await seed(ended_rows(30))

        for order in ("asc", "desc"):
            for by in ("price", "timestamp"):
                r = await client.get(f"/auctions/ended?limit=5&sort_by={by}&sort_order={order}")
                assert r.json()["total"] == 30

        # Sorting reorders rows; it never resizes the set.
        assert len(await redis.keys("auction_count:*")) == 1

    async def test_paging_shares_one_cached_count(self, client, seed, redis, stub_names):
        await seed(ended_rows(30))

        for skip in (0, 5, 10):
            r = await client.get(f"/auctions/ended?limit=5&skip={skip}")
            assert r.json()["total"] == 30

        assert len(await redis.keys("auction_count:*")) == 1


class TestStatsCaching:
    """/auctions/stats is four full passes over the table on every request.

    The cache stores the assembled payload, so the round trip through JSON has
    to survive the nested price buckets -- that is the part most likely to
    break silently.
    """

    async def test_a_cache_hit_returns_the_same_payload(self, client, seed, redis):
        await seed(ended_rows(30))

        first = await client.get("/auctions/stats")
        assert len(await redis.keys("auction_stats:*")) == 1

        second = await client.get("/auctions/stats")
        assert second.json() == first.json()

    async def test_price_buckets_survive_the_cache(self, client, seed, redis):
        await seed(ended_rows(60))

        fresh = (await client.get("/auctions/stats")).json()["price_distribution"]
        cached = (await client.get("/auctions/stats")).json()["price_distribution"]

        assert fresh, "expected a distribution to actually be exercised"
        assert cached == fresh
        for bucket in cached:
            assert set(bucket) == {"label", "count", "min_price", "max_price"}

    async def test_rows_added_behind_the_cache_do_not_change_it(self, client, seed, redis):
        await seed(ended_rows(30))
        assert (await client.get("/auctions/stats")).json()["total"] == 30

        await seed([EndedAuction(
            auction_id="extra", seller_uuid="s", buyer_uuid="b", price=1, is_bin=True,
            item_uuid="i", hex_color="FF0000", piece=0, timestamp=BASE_MS,
            target_hex_keys="FF0102", delta_e_keys=0.0, tier_keys=get_tier(0.0),
            target_hex_all="FF0000", delta_e_all=0.0, tier_all=get_tier(0.0),
        )])

        assert (await client.get("/auctions/stats")).json()["total"] == 30

    async def test_a_different_filter_gets_its_own_stats(self, client, seed, redis):
        await seed(ended_rows(30))

        everything = (await client.get("/auctions/stats")).json()["total"]
        one_piece = (await client.get("/auctions/stats?pieces=0")).json()["total"]

        assert everything == 30
        assert one_piece < everything
        assert len(await redis.keys("auction_stats:*")) == 2

    async def test_the_empty_result_is_cacheable(self, client, seed, redis):
        await seed(ended_rows(5))

        empty = (await client.get("/auctions/stats?min_price=999999999")).json()
        assert empty["total"] == 0
        assert empty["price_distribution"] == []
        assert empty["hourly_activity"] == [0] * 24

        assert (await client.get("/auctions/stats?min_price=999999999")).json() == empty

    async def test_stats_and_count_caches_do_not_collide(self, client, seed, redis, stub_names):
        await seed(ended_rows(30))

        await client.get("/auctions/ended?limit=5")
        await client.get("/auctions/stats")

        assert len(await redis.keys("auction_count:*")) == 1
        assert len(await redis.keys("auction_stats:*")) == 1
