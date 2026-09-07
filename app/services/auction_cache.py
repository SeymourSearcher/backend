"""Rebuilding the auction caches from the sync loop instead of from a request.

Both entries describe a whole filtered row set, so nothing in them depends on
who is asking -- yet their TTL used to equal the sync interval, which handed the
rebuild to whichever request arrived first in each window. They are dropped by
the sync that changes the rows instead.

Only entries that are still cached are rebuilt. Presence is the hotness signal:
a live key means somebody read it inside the TTL, so recomputing it now saves a
real user the wait, while an absent one means nobody is looking and an idle site
should not spend ~800ms of MySQL a minute keeping a page warm for no one.
"""
import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActiveAuction, EndedAuction
from app.services.auction_queries import (
    AuctionFilters,
    build_where,
    count_auctions,
    filters_key,
)
from app.services.auction_stats import compute_auction_stats
from app.services.cache import (
    AUCTION_COUNT,
    AUCTION_STATS,
    COLLECTION_VALUE,
    HEX_PRICES,
    TOP_SALES,
    CacheEntry,
)

logger = logging.getLogger(__name__)

# The auctions page's opening state. The dyes toggle is one click away and flips
# which tier columns are read, so both sides of it count as default.
DEFAULT_FILTERS = [AuctionFilters(include_dyes=True), AuctionFilters(include_dyes=False)]


def _table_prefix(model) -> str:
    """Counts for both tables share one namespace; see filters_key."""
    return f"{model.__tablename__}:"


async def _hot(redis: Redis, entry: CacheEntry, model) -> list[AuctionFilters]:
    return [f for f in DEFAULT_FILTERS if await entry.exists(redis, filters_key(model, f))]


async def refresh_ended(db: AsyncSession, redis: Redis) -> None:
    """Call after a sync that wrote ended_auctions rows."""
    hot_stats = await _hot(redis, AUCTION_STATS, EndedAuction)
    hot_counts = await _hot(redis, AUCTION_COUNT, EndedAuction)

    # Every stats entry is over ended_auctions, so none of them survive.
    await AUCTION_STATS.invalidate_all(redis)
    await AUCTION_COUNT.invalidate_all(redis, _table_prefix(EndedAuction))
    await HEX_PRICES.invalidate_all(redis)
    await TOP_SALES.invalidate_all(redis)
    # Priced from the rows that just changed, so it cannot outlive them.
    await COLLECTION_VALUE.invalidate_all(redis)

    for filters in hot_stats:
        await compute_auction_stats(db, filters, redis=redis)
    for filters in hot_counts:
        await count_auctions(db, redis, EndedAuction, filters, build_where(EndedAuction, filters))


async def refresh_active(db: AsyncSession, redis: Redis) -> None:
    """Call after a sync that added or removed active_auctions rows."""
    hot_counts = await _hot(redis, AUCTION_COUNT, ActiveAuction)

    await AUCTION_COUNT.invalidate_all(redis, _table_prefix(ActiveAuction))

    for filters in hot_counts:
        await count_auctions(db, redis, ActiveAuction, filters, build_where(ActiveAuction, filters))


async def invalidate_all(redis: Redis) -> None:
    """Every auction cache entry, for writers outside the sync loop."""
    await AUCTION_STATS.invalidate_all(redis)
    await AUCTION_COUNT.invalidate_all(redis)
    await TOP_SALES.invalidate_all(redis)
