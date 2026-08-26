"""Aggregate statistics over ended auctions.

Everything is computed by MySQL over the full matching set -- no row limit and
no bulk transfer into Python. The percentile-trimmed price distribution selects
rows by ROW_NUMBER() rather than by price range, which reproduces the positional
list slicing the buckets are defined by, including when ties straddle a
percentile boundary.

Four passes over the table, ~800ms unfiltered, so the whole result is cached.
The two ungrouped passes look mergeable into one GROUP BY hour -- summing the
24 groups reproduces every scalar exactly -- but it was measured and the
grouping costs back what the saved scan wins (465ms apart, 485ms merged).
The two ROW_NUMBER passes cannot merge at all: the histogram's bucket bounds
come out of the percentile query.
"""
from redis.asyncio import Redis
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EndedAuction
from app.schemas.auction import PriceBucket
from app.services.auction_queries import (
    AuctionFilters,
    build_where,
    filters_key,
    match_columns_for,
)
from app.services.cache import AUCTION_STATS
from app.services.matching import TIER_THRESHOLDS

BUCKET_COUNT = 8
LOWER_PCT = 0.05
UPPER_PCT = 0.95

# Below this many rows the distribution collapses to a single bucket, so the
# prices are cheap to just fetch.
SMALL_SAMPLE = 10

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


def _ranked(where):
    """Matching rows numbered 1..n by ascending price."""
    return (
        select(
            EndedAuction.price.label("price"),
            func.row_number().over(order_by=EndedAuction.price).label("rn"),
        )
        .where(where)
        .subquery()
    )


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


async def _prices_at(db: AsyncSession, where, positions: set[int]) -> dict[int, int]:
    """Prices at specific 1-based positions in the sorted order."""
    wanted = sorted(positions)
    if not wanted:
        return {}
    ranked = _ranked(where)
    rows = (
        await db.execute(select(ranked.c.rn, ranked.c.price).where(ranked.c.rn.in_(wanted)))
    ).all()
    return {int(rn): int(price) for rn, price in rows}


async def _all_prices(db: AsyncSession, where) -> list[int]:
    result = await db.execute(
        select(EndedAuction.price).where(where).order_by(EndedAuction.price)
    )
    return list(result.scalars().all())


async def _middle_histogram(
    db: AsyncSession, where, lower_idx: int, upper_idx: int, mid_min: int,
    bucket_size: float, bucket_count: int,
) -> list[int]:
    ranked = _ranked(where)
    bucket = func.least(
        bucket_count - 1,
        func.floor((ranked.c.price - mid_min) / bucket_size),
    ).label("bucket")

    rows = (
        await db.execute(
            select(bucket, func.count())
            .where(ranked.c.rn > lower_idx, ranked.c.rn <= upper_idx)
            .group_by(bucket)
        )
    ).all()

    counts = [0] * bucket_count
    for index, count in rows:
        counts[int(index)] = int(count)
    return counts


def _median_from(at: dict[int, int], n: int) -> float:
    if n == 0:
        return 0.0
    if n % 2 == 0:
        return (at[n // 2] + at[n // 2 + 1]) / 2
    return float(at[n // 2 + 1])


def _small_sample_buckets(prices: list[int]) -> list[PriceBucket]:
    n = len(prices)
    if n == 0:
        return []
    min_price, max_price = prices[0], prices[-1]
    if min_price == max_price:
        return [PriceBucket(label=f"{min_price:,}", count=n, min_price=min_price, max_price=max_price)]
    return [PriceBucket(
        label=f"{format_price(min_price)} - {format_price(max_price)}",
        count=n, min_price=min_price, max_price=max_price,
    )]


def _assemble_buckets(
    counts: list[int], lower_count: int, upper_count: int,
    first_price: int, last_price: int, mid_min: int, mid_max: int, bucket_size: float,
) -> list[PriceBucket]:
    buckets = []
    if lower_count:
        buckets.append(PriceBucket(
            label=f"< {format_price(mid_min)}",
            count=lower_count, min_price=first_price, max_price=mid_min - 1,
        ))

    if mid_max == mid_min:
        buckets.append(PriceBucket(
            label=f"{mid_min:,}", count=sum(counts), min_price=mid_min, max_price=mid_max,
        ))
    else:
        for i, count in enumerate(counts):
            bucket_min = int(mid_min + i * bucket_size)
            bucket_max = int(mid_min + (i + 1) * bucket_size) if i < len(counts) - 1 else mid_max + 1
            buckets.append(PriceBucket(
                label=f"{format_price(bucket_min)} - {format_price(bucket_max)}",
                count=count, min_price=bucket_min, max_price=bucket_max,
            ))

    if upper_count:
        buckets.append(PriceBucket(
            label=f"> {format_price(mid_max)}",
            count=upper_count, min_price=mid_max + 1, max_price=last_price,
        ))
    return buckets


async def _price_stats(db: AsyncSession, where, n: int) -> tuple[float, list[PriceBucket]]:
    if n == 0:
        return 0.0, []

    if n < SMALL_SAMPLE:
        prices = await _all_prices(db, where)
        return _median_from({i + 1: p for i, p in enumerate(prices)}, n), _small_sample_buckets(prices)

    lower_idx, upper_idx = int(n * LOWER_PCT), int(n * UPPER_PCT)
    if upper_idx <= lower_idx:
        return 0.0, []

    at = await _prices_at(db, where, {1, n, lower_idx + 1, upper_idx, n // 2, n // 2 + 1})
    median = _median_from(at, n)

    mid_min, mid_max = at[lower_idx + 1], at[upper_idx]
    middle_count = upper_idx - lower_idx

    if mid_max == mid_min:
        counts, bucket_size = [middle_count], 0.0
    else:
        bucket_count = min(BUCKET_COUNT - 2, max(4, middle_count // 10))
        bucket_size = (mid_max - mid_min) / bucket_count
        counts = await _middle_histogram(
            db, where, lower_idx, upper_idx, mid_min, bucket_size, bucket_count
        )

    buckets = _assemble_buckets(
        counts, lower_idx, n - upper_idx, at[1], at[n], mid_min, mid_max, bucket_size
    )
    return median, buckets


async def _compute(db: AsyncSession, filters: AuctionFilters) -> dict:
    where = build_where(EndedAuction, filters)
    _, _, tier_col = match_columns_for(EndedAuction, filters.include_dyes)

    stats = await _scalar_aggregates(db, where, tier_col)
    if stats["total"] == 0:
        return {**stats, "median_price": 0.0, "price_distribution": [], "hourly_activity": [0] * 24}

    median, buckets = await _price_stats(db, where, stats["total"])
    return {
        **stats,
        "median_price": round(median, 2),
        "price_distribution": buckets,
        "hourly_activity": await _hourly_activity(db, where),
    }


def _encode(stats: dict) -> dict:
    return {**stats, "price_distribution": [b.model_dump() for b in stats["price_distribution"]]}


def _decode(stats: dict) -> dict:
    return {**stats, "price_distribution": [PriceBucket(**b) for b in stats["price_distribution"]]}


async def compute_auction_stats(
    db: AsyncSession, filters: AuctionFilters, redis: Redis | None = None
) -> dict:
    """Cached on the filters, which are the only thing that changes the result.

    TTL matches ENDED_SYNC_INTERVAL, so the figures are never staler than the
    rows they describe. Buckets are converted at the cache boundary only, so a
    hit and a miss return the same PriceBucket-shaped payload either way.
    """
    if redis is None:
        return await _compute(db, filters)

    key = filters_key(EndedAuction, filters)
    cached = await AUCTION_STATS.get(redis, key)
    if cached is not None:
        return _decode(cached)

    stats = await _compute(db, filters)
    await AUCTION_STATS.set(redis, _encode(stats), key)
    return stats
