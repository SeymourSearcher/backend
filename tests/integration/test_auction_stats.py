"""Differential cover for the stats rewrite.

Every number here used to be computed in Python over rows pulled out of MySQL
(capped at 50k, so `total` was a lie on any large filter). It is now computed by
MySQL. These tests recompute the same values in plain Python over the seeded
rows and require the two to agree, which is the only thing that makes the
rewrite safe to trust.
"""
import pytest

from app.models import EndedAuction
from app.services.auction_queries import AuctionFilters
from app.services.auction_stats import LOWER_PCT, UPPER_PCT, compute_auction_stats
from app.services.matching import get_tier

pytestmark = pytest.mark.integration

BASE_MS = 1_700_000_000_000
HOUR_MS = 3_600_000


def make_rows(count):
    """Deterministic spread: prices, tiers, bin flags and hours all vary."""
    rows = []
    for i in range(count):
        delta_e = (i % 20) * 0.7  # spans t1/t2/t3 and past the t3 ceiling
        rows.append(EndedAuction(
            auction_id=f"a{i:05d}",
            seller_uuid="s", buyer_uuid="b",
            price=100 + (i * 137) % 9_000,
            is_bin=(i % 3 == 0),
            item_uuid=f"i{i}",
            hex_color="FF0000",
            piece=i % 4,
            timestamp=BASE_MS + i * HOUR_MS,
            target_hex_keys="FF0102", delta_e_keys=delta_e, tier_keys=get_tier(delta_e),
            target_hex_all="FF0000", delta_e_all=delta_e, tier_all=get_tier(delta_e),
        ))
    return rows


def reference(rows):
    """The stats, computed the obvious way in Python."""
    prices = sorted(r.price for r in rows)
    n = len(prices)
    bins = sum(1 for r in rows if r.is_bin)

    hours = [0] * 24
    for row in rows:
        seconds = row.timestamp / 1000 if row.timestamp > 9999999999 else row.timestamp
        hours[int(seconds // 3600) % 24] += 1

    if n == 0:
        median = 0.0
    elif n % 2:
        median = float(prices[n // 2])
    else:
        median = (prices[n // 2 - 1] + prices[n // 2]) / 2

    return {
        "total": n,
        "avg_price": round(sum(prices) / n, 2) if n else 0.0,
        "total_volume": sum(prices),
        "bin_count": bins,
        "auction_count": n - bins,
        "t1_count": sum(1 for r in rows if r.tier_all == "t1"),
        "t2_count": sum(1 for r in rows if r.tier_all == "t2"),
        "t3_count": sum(1 for r in rows if r.tier_all == "t3"),
        "median_price": round(median, 2),
        "hourly_activity": hours,
    }


@pytest.fixture
async def seed(db):
    async def _seed(rows):
        db.add_all(rows)
        await db.commit()
        return rows
    return _seed


@pytest.mark.parametrize("count", [1, 2, 9, 10, 11, 250])
async def test_scalar_stats_match_python(db, seed, count):
    rows = await seed(make_rows(count))
    stats = await compute_auction_stats(db, AuctionFilters())
    expected = reference(rows)

    for key, value in expected.items():
        assert stats[key] == value, key


async def test_empty_set_returns_zeroes_not_an_error(db, clean_db):
    stats = await compute_auction_stats(db, AuctionFilters())

    assert stats["total"] == 0
    assert stats["median_price"] == 0.0
    assert stats["price_distribution"] == []
    assert stats["hourly_activity"] == [0] * 24


async def test_buckets_account_for_every_row(db, seed):
    rows = await seed(make_rows(250))
    stats = await compute_auction_stats(db, AuctionFilters())

    assert sum(b.count for b in stats["price_distribution"]) == len(rows)


async def test_bucket_tails_hold_exactly_the_trimmed_rows(db, seed):
    rows = await seed(make_rows(250))
    n = len(rows)
    stats = await compute_auction_stats(db, AuctionFilters())

    buckets = stats["price_distribution"]
    assert buckets[0].count == int(n * LOWER_PCT)
    assert buckets[-1].count == n - int(n * UPPER_PCT)


async def test_buckets_are_ordered_by_price(db, seed):
    await seed(make_rows(250))
    stats = await compute_auction_stats(db, AuctionFilters())

    mins = [b.min_price for b in stats["price_distribution"]]
    assert mins == sorted(mins)


async def test_tier_filter_narrows_every_number(db, seed):
    rows = await seed(make_rows(250))
    stats = await compute_auction_stats(db, AuctionFilters(tier="t2", include_dyes=True))
    expected = reference([r for r in rows if r.tier_all == "t2"])

    assert stats["total"] == expected["total"] < len(rows)
    assert stats["median_price"] == expected["median_price"]
    assert stats["total_volume"] == expected["total_volume"]


async def test_filters_compose(db, seed):
    rows = await seed(make_rows(250))
    filters = AuctionFilters(pieces=[0, 1], min_price=1_000, max_price=8_000, bin_filter="bin")
    stats = await compute_auction_stats(db, filters)

    expected = reference([
        r for r in rows
        if r.piece in (0, 1) and 1_000 <= r.price <= 8_000 and r.is_bin
    ])
    assert stats["total"] == expected["total"]
    assert stats["total_volume"] == expected["total_volume"]
    assert stats["median_price"] == expected["median_price"]


async def test_unmatched_filter_is_empty_not_an_error(db, seed):
    await seed(make_rows(50))
    stats = await compute_auction_stats(db, AuctionFilters(tier="t9"))

    assert stats["total"] == 0
    assert stats["price_distribution"] == []


async def test_hour_bucketing_handles_second_and_millisecond_rows(db, seed):
    """Old rows stored seconds, new ones milliseconds; both must land in the
    same hour-of-day bucket rather than one of them landing in 1970."""
    seconds_row = make_rows(1)[0]
    seconds_row.auction_id = "sec"
    seconds_row.timestamp = BASE_MS // 1000

    ms_row = make_rows(1)[0]
    ms_row.auction_id = "ms"
    ms_row.timestamp = BASE_MS

    await seed([seconds_row, ms_row])
    stats = await compute_auction_stats(db, AuctionFilters())

    expected_hour = int((BASE_MS / 1000) // 3600) % 24
    assert stats["hourly_activity"][expected_hour] == 2
    assert sum(stats["hourly_activity"]) == 2


async def test_include_dyes_selects_the_other_tier_column(db, seed):
    rows = make_rows(60)
    # Force the two variants apart: keys says t3, all says t1.
    for row in rows:
        row.tier_keys, row.delta_e_keys = "t3", 7.0
        row.tier_all, row.delta_e_all = "t1", 1.0
    await seed(rows)

    with_dyes = await compute_auction_stats(db, AuctionFilters(tier="t1", include_dyes=True))
    without = await compute_auction_stats(db, AuctionFilters(tier="t1", include_dyes=False))

    assert with_dyes["total"] == 60
    assert without["total"] == 0
