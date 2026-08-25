"""Aggregate statistics over ended auctions.

Scalar aggregates and the hourly histogram are computed by MySQL over the full
matching set. Only the price column is pulled into Python, for the median and
the percentile-trimmed price distribution, whose tie handling at the percentile
boundaries does not translate cleanly to SQL.
"""
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EndedAuction
from app.schemas.auction import PriceBucket
from app.services.auction_queries import AuctionFilters, build_where, match_columns_for
from app.services.matching import TIER_THRESHOLDS

BUCKET_COUNT = 8
LOWER_PCT = 0.05
UPPER_PCT = 0.95

# Hypixel timestamps are milliseconds; older rows were stored as seconds.
_MS_CUTOFF = 9999999999


def format_price(price: int) -> str:
    if price >= 1_000_000:
        return f"{price / 1_000_000:.1f}M"
    if price >= 1_000:
        return f"{price // 1_000}K"
    return str(price)


def _epoch_seconds():
    ts = EndedAuction.timestamp
    return case((ts > _MS_CUTOFF, ts / 1000), else_=ts)


def _utc_hour():
    """Hour-of-day in UTC, computed arithmetically to avoid MySQL's session tz."""
    return func.floor(_epoch_seconds() / 3600) % 24


async def _scalar_aggregates(db: AsyncSession, where, tier_col) -> dict:
    def tier_count(tier: str):
        return func.coalesce(func.sum(case((tier_col == tier, 1), else_=0)), 0)

    row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.coalesce(func.avg(EndedAuction.price), 0).label("avg_price"),
                func.coalesce(func.sum(EndedAuction.price), 0).label("total_volume"),
                func.coalesce(
                    func.sum(case((EndedAuction.is_bin.is_(True), 1), else_=0)), 0
                ).label("bin_count"),
                *[tier_count(t).label(f"{t}_count") for t in TIER_THRESHOLDS],
            ).where(where)
        )
    ).one()

    return {
        "total": int(row.total),
        "avg_price": round(float(row.avg_price), 2),
        "total_volume": int(row.total_volume),
        "bin_count": int(row.bin_count),
        "auction_count": int(row.total) - int(row.bin_count),
        **{f"{t}_count": int(getattr(row, f"{t}_count")) for t in TIER_THRESHOLDS},
    }


async def _hourly_activity(db: AsyncSession, where) -> list[int]:
    hour = _utc_hour().label("hour")
    rows = (await db.execute(select(hour, func.count()).where(where).group_by(hour))).all()

    hours = [0] * 24
    for hour_value, count in rows:
        hours[int(hour_value)] = int(count)
    return hours


async def _prices(db: AsyncSession, where) -> list[int]:
    result = await db.execute(
        select(EndedAuction.price).where(where).order_by(EndedAuction.price)
    )
    return list(result.scalars().all())


def _median(prices: list[int]) -> float:
    if not prices:
        return 0.0
    mid = len(prices) // 2
    if len(prices) % 2 == 0:
        return (prices[mid - 1] + prices[mid]) / 2
    return float(prices[mid])


def compute_price_buckets(prices: list[int]) -> list[PriceBucket]:
    """`prices` must be sorted ascending."""
    n = len(prices)
    if n == 0:
        return []

    if n < 10:
        min_price, max_price = prices[0], prices[-1]
        if min_price == max_price:
            return [PriceBucket(label=f"{min_price:,}", count=n, min_price=min_price, max_price=max_price)]
        return [PriceBucket(
            label=f"{format_price(min_price)} - {format_price(max_price)}",
            count=n, min_price=min_price, max_price=max_price,
        )]

    lower_idx, upper_idx = int(n * LOWER_PCT), int(n * UPPER_PCT)
    lower_prices = prices[:lower_idx]
    middle_prices = prices[lower_idx:upper_idx]
    upper_prices = prices[upper_idx:]

    if not middle_prices:
        return []

    buckets = []
    if lower_prices:
        buckets.append(PriceBucket(
            label=f"< {format_price(middle_prices[0])}",
            count=len(lower_prices),
            min_price=lower_prices[0],
            max_price=middle_prices[0] - 1,
        ))

    mid_min, mid_max = middle_prices[0], middle_prices[-1]
    mid_range = mid_max - mid_min

    if mid_range == 0:
        buckets.append(PriceBucket(
            label=f"{mid_min:,}", count=len(middle_prices), min_price=mid_min, max_price=mid_max,
        ))
    else:
        mid_bucket_count = min(BUCKET_COUNT - 2, max(4, len(middle_prices) // 10))
        bucket_size = mid_range / mid_bucket_count
        counts = [0] * mid_bucket_count
        for price in middle_prices:
            counts[min(mid_bucket_count - 1, int((price - mid_min) / bucket_size))] += 1

        for i, count in enumerate(counts):
            bucket_min = int(mid_min + i * bucket_size)
            bucket_max = int(mid_min + (i + 1) * bucket_size) if i < mid_bucket_count - 1 else mid_max + 1
            buckets.append(PriceBucket(
                label=f"{format_price(bucket_min)} - {format_price(bucket_max)}",
                count=count, min_price=bucket_min, max_price=bucket_max,
            ))

    if upper_prices:
        buckets.append(PriceBucket(
            label=f"> {format_price(middle_prices[-1])}",
            count=len(upper_prices),
            min_price=middle_prices[-1] + 1,
            max_price=upper_prices[-1],
        ))

    return buckets


async def compute_auction_stats(db: AsyncSession, filters: AuctionFilters) -> dict:
    where = build_where(EndedAuction, filters)
    _, _, tier_col = match_columns_for(EndedAuction, filters.include_dyes)

    stats = await _scalar_aggregates(db, where, tier_col)
    if stats["total"] == 0:
        return {**stats, "median_price": 0.0, "price_distribution": [], "hourly_activity": [0] * 24}

    prices = await _prices(db, where)
    return {
        **stats,
        "median_price": round(_median(prices), 2),
        "price_distribution": compute_price_buckets(prices),
        "hourly_activity": await _hourly_activity(db, where),
    }
