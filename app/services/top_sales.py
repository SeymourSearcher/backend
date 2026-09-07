"""The highest-priced sales of the last day, for the homepage ticker.

Deliberately not a filter on the auctions endpoint: the window is a moving
timestamp, so every request would key its own cache entry and none would ever
be reused. One entry per (hours, limit, dyes) is dropped by the sync that
writes the rows instead -- see app/services/auction_cache.py.
"""
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_ms
from app.models import EndedAuction
from app.schemas.auction import EndedAuctionResponse
from app.services.auction_queries import serialize
from app.services.cache import TOP_SALES

WINDOW_HOURS = 24
TOP_COUNT = 20

_HOUR_MS = 3_600_000


def window_start_ms(hours: int) -> int:
    """Rows written in seconds rather than milliseconds fall far below this and
    drop out, which is what a recency window wants anyway -- see the _MS_CUTOFF
    note in app/services/auction_stats.py."""
    return utcnow_ms() - hours * _HOUR_MS


async def get_top_sales(
    db: AsyncSession,
    redis: Redis,
    hours: int = WINDOW_HOURS,
    limit: int = TOP_COUNT,
    include_dyes: bool = True,
) -> list[dict]:
    """Cached: the window edge moves while the entry lives, but a quarter hour
    of drift against a day-long window cannot change which sales are the top
    twenty."""
    cached = await TOP_SALES.get(redis, hours, limit, include_dyes)
    if cached is not None:
        return cached

    rows = await db.execute(
        select(EndedAuction)
        .where(EndedAuction.timestamp >= window_start_ms(hours))
        .order_by(EndedAuction.price.desc())
        .limit(limit)
    )
    sales = [
        serialize(a, EndedAuctionResponse, include_dyes).model_dump(mode="json")
        for a in rows.scalars().all()
    ]

    await TOP_SALES.set(redis, sales, hours, limit, include_dyes)
    return sales
