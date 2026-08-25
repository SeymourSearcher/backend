"""The pure half of the stats rewrite: bucket assembly and positional median.

_median_from mirrors Python list slicing over the SQL-ranked rows, so its
even/odd handling is the thing most likely to drift from the old implementation.
"""
import pytest

from app.services.auction_stats import (
    _assemble_buckets,
    _median_from,
    _small_sample_buckets,
    format_price,
)


@pytest.mark.parametrize("price, expected", [
    (0, "0"),
    (999, "999"),
    (1_000, "1K"),
    (1_500, "1K"),
    (999_999, "999K"),
    (1_000_000, "1.0M"),
    (2_450_000, "2.5M"),
])
def test_format_price(price, expected):
    assert format_price(price) == expected


def positions(prices):
    return {i + 1: p for i, p in enumerate(sorted(prices))}


@pytest.mark.parametrize("prices", [
    [5],
    [1, 3],
    [1, 2, 3],
    [1, 2, 3, 4],
    [10, 10, 10, 10, 10],
    [1, 1, 2, 100, 100],
])
def test_median_matches_the_positional_definition(prices):
    prices = sorted(prices)
    n = len(prices)
    expected = (
        prices[n // 2] if n % 2
        else (prices[n // 2 - 1] + prices[n // 2]) / 2
    )
    assert _median_from(positions(prices), n) == expected


def test_median_of_nothing_is_zero():
    assert _median_from({}, 0) == 0.0


def test_small_sample_buckets():
    assert _small_sample_buckets([]) == []

    single = _small_sample_buckets([500, 500, 500])
    assert len(single) == 1
    assert single[0].count == 3
    assert single[0].label == "500"

    spread = _small_sample_buckets([100, 250, 900])
    assert len(spread) == 1
    assert spread[0].count == 3
    assert (spread[0].min_price, spread[0].max_price) == (100, 900)


def test_assemble_buckets_counts_are_preserved():
    buckets = _assemble_buckets(
        counts=[3, 5, 2], lower_count=4, upper_count=6,
        first_price=10, last_price=9999, mid_min=100, mid_max=400, bucket_size=100.0,
    )

    assert [b.count for b in buckets] == [4, 3, 5, 2, 6]
    assert buckets[0].label.startswith("<")
    assert buckets[-1].label.startswith(">")


def test_assemble_buckets_omits_empty_tails():
    buckets = _assemble_buckets(
        counts=[7], lower_count=0, upper_count=0,
        first_price=100, last_price=200, mid_min=100, mid_max=200, bucket_size=100.0,
    )
    assert [b.count for b in buckets] == [7]
    assert not any(b.label.startswith(("<", ">")) for b in buckets)


def test_assemble_buckets_collapses_when_the_middle_is_one_price():
    buckets = _assemble_buckets(
        counts=[9], lower_count=1, upper_count=1,
        first_price=1, last_price=99, mid_min=50, mid_max=50, bucket_size=0.0,
    )
    middle = [b for b in buckets if not b.label.startswith(("<", ">"))]
    assert len(middle) == 1
    assert middle[0].count == 9
    assert middle[0].min_price == middle[0].max_price == 50


def test_assemble_buckets_leave_no_gap_in_the_price_range():
    buckets = _assemble_buckets(
        counts=[1, 1, 1, 1], lower_count=2, upper_count=2,
        first_price=0, last_price=1000, mid_min=100, mid_max=500, bucket_size=100.0,
    )
    low, middle, high = buckets[0], buckets[1:-1], buckets[-1]

    assert (low.min_price, low.max_price) == (0, 99)
    # Middle buckets are half-open [min, max), so neighbours share a boundary.
    assert [(b.min_price, b.max_price) for b in middle] == [
        (100, 200), (200, 300), (300, 400), (400, 501),
    ]
    assert (high.min_price, high.max_price) == (501, 1000)
