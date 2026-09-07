"""median_prices_for: many (target, tier) pairs in one query.

The hex page and the /items valuation both price through this, so the number it
returns has to be the same one a person would get by sorting the sales by hand.
Pinned against a plain Python median rather than against another SQL expression,
so a rewrite of the query cannot quietly redefine "median" for both callers at
once.
"""
import pytest

from app.models import EndedAuction
from app.services.hex_prices import median_prices_for, price_estimates

pytestmark = pytest.mark.integration

BASE_MS = 1_700_000_000_000


def sale(auction_id: str, target_hex: str, tier: str, price: int, piece: int = 0):
    return EndedAuction(
        auction_id=auction_id, seller_uuid="s", buyer_uuid="b",
        price=price, is_bin=False, item_uuid=auction_id,
        hex_color=target_hex, piece=piece, timestamp=BASE_MS,
        target_hex_keys=target_hex, delta_e_keys=0.5, tier_keys=tier,
        target_hex_all=target_hex, delta_e_all=0.5, tier_all=tier,
    )


def lower_median(prices: list[int]) -> float:
    """The single row at index n // 2 of the sorted list.

    Not the mean of the middle two -- auction_stats does that, this does not,
    and the difference is only visible on even-sized samples.
    """
    return float(sorted(prices)[len(prices) // 2])


@pytest.fixture
async def seed(db):
    async def _seed(rows):
        db.add_all(rows)
        await db.commit()
    return _seed


# Odd, even, and a single sale: the even case is the one that would expose an
# averaging median, and the single case the one that would expose an off-by-one.
SPREADS = {
    ("AAAAAA", "t1"): [100, 500, 300],
    ("BBBBBB", "t1"): [10, 20, 30, 40],
    ("CCCCCC", "t2"): [777],
    ("DDDDDD", "t3"): [5, 5, 5, 5, 5, 9],
}


@pytest.fixture
async def spread(seed):
    rows = []
    for i, ((target, tier), prices) in enumerate(SPREADS.items()):
        for j, price in enumerate(prices):
            rows.append(sale(f"a{i}{j:03d}", target, tier, price))
    await seed(rows)
    return SPREADS


async def test_every_pair_gets_the_hand_computed_median(db, spread):
    result = await median_prices_for(db, list(spread), include_dyes=True)

    for pair, prices in spread.items():
        assert result[pair]["median"] == lower_median(prices), pair
        assert result[pair]["sales"] == len(prices), pair


async def test_a_pair_with_no_sales_is_absent_rather_than_zero(db, spread):
    """Absent and priced-at-nothing are different answers, and the valuation
    excludes the first while summing the second."""
    result = await median_prices_for(db, [("EEEEEE", "t1")], include_dyes=True)

    assert result == {}


async def test_a_tier_that_never_matched_is_dropped_before_querying(db, spread):
    """A colour past the last tier has no band to compare against."""
    assert await median_prices_for(db, [("AAAAAA", None)], include_dyes=True) == {}


async def test_pairs_do_not_bleed_into_each_other(db, spread):
    """Same target, different tiers must partition separately -- one index
    covers target alone, so tier is a post-filter and easy to lose."""
    await db.execute(
        EndedAuction.__table__.insert().values(
            auction_id="mixed", seller_uuid="s", buyer_uuid="b",
            price=999_999, is_bin=False, item_uuid="mixed",
            hex_color="AAAAAA", piece=0, timestamp=BASE_MS,
            target_hex_keys="AAAAAA", delta_e_keys=6.0, tier_keys="t3",
            target_hex_all="AAAAAA", delta_e_all=6.0, tier_all="t3",
        )
    )
    await db.commit()

    result = await median_prices_for(db, [("AAAAAA", "t1"), ("AAAAAA", "t3")], include_dyes=True)

    assert result[("AAAAAA", "t1")]["sales"] == 3
    assert result[("AAAAAA", "t3")]["sales"] == 1
    assert result[("AAAAAA", "t3")]["median"] == 999_999.0


async def test_the_piece_filter_narrows_the_partition(db, seed):
    await seed([
        sale("p0", "FFFFFF", "t1", 100, piece=0),
        sale("p1", "FFFFFF", "t1", 200, piece=1),
        sale("p2", "FFFFFF", "t1", 300, piece=1),
    ])

    helmets = await median_prices_for(db, [("FFFFFF", "t1")], True, pieces=[0])
    chests = await median_prices_for(db, [("FFFFFF", "t1")], True, pieces=[1])

    assert helmets[("FFFFFF", "t1")] == {"median": 100.0, "sales": 1}
    assert chests[("FFFFFF", "t1")]["sales"] == 2


async def test_price_estimates_preserves_the_order_it_was_asked_in(db, redis, spread):
    """It returns a list positionally matched to its input, not a mapping."""
    targets = [("CCCCCC", "t2"), ("EEEEEE", "t1"), ("AAAAAA", "t1")]

    estimates = await price_estimates(db, redis, targets, include_dyes=True, pieces=None)

    assert [e["median"] for e in estimates] == [777.0, None, 300.0]
    assert [e["sales"] for e in estimates] == [1, 0, 3]
