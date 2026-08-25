"""Auction listing endpoints.

Filtering, sorting and pagination all moved into SQL, and `total` became a real
COUNT(*) instead of the length of a truncated Python list. The pagination test
is the one that matters most: OFFSET over a non-unique sort key silently repeats
and skips rows unless the query carries a tiebreaker.
"""
import pytest

from app.models import ActiveAuction, EndedAuction
from app.services.matching import get_tier

pytestmark = pytest.mark.integration

BASE_MS = 1_700_000_000_000


@pytest.fixture(autouse=True)
def no_upstream_lookups(monkeypatch):
    """attach_names would otherwise call the live username API."""
    async def fake(db, uuids):
        return {uuid: f"player-{uuid[:4]}" for uuid in uuids}

    monkeypatch.setattr("app.services.auction_queries.get_player_names", fake)


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
