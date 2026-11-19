"""Keeping the auction caches fresh from the sync loop instead of from a request.

Two kinds of entry, two policies:

- Row-level entries (the ended/active counts, the top-sales ticker) describe
  rows a visitor can see change, so the sync that changes them drops them. Hot
  counts are rebuilt straight away. Presence is the hotness signal: a live key
  means somebody read it inside the TTL, while an absent one means nobody is
  looking and an idle site should not keep it warm.
- Aggregates over all sales (stats, hex median prices, collection values) are
  left to their TTL. One more sale out of ~250k does not visibly move them, and
  dropping them on every sale (one every ~200s in prod) meant the TTL never
  applied and the next visitor paid a 1.3-2.3s recompute. Instead the worker
  rebuilds the stats pages people actually land on shortly before they expire
  (warm_stats), so those never miss at all.
"""
import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActiveAuction, EndedAuction
from app.pieces import PIECE_COUNT
from app.services.auction_queries import (
    AuctionFilters,
    build_where,
    count_auctions,
    filters_key,
)
from app.services.auction_stats import rebuild_auction_stats
from app.services.cache import (
    AUCTION_COUNT,
    AUCTION_STATS,
    TOP_SALES,
    CacheEntry,
)

logger = logging.getLogger(__name__)

# The auctions page's opening state. The dyes toggle is one click away and flips
# which tier columns are read, so both sides of it count as default.
DEFAULT_FILTERS = [AuctionFilters(include_dyes=True), AuctionFilters(include_dyes=False)]

# The auctions page defaults plus the per-piece pages (/auctions/stats?pieces=N).
WARM_STATS = DEFAULT_FILTERS + [AuctionFilters(pieces=[piece]) for piece in range(PIECE_COUNT)]

# Comfortably more than the ended sync interval, so the worker always gets to an
# entry before it expires.
REWARM_MARGIN_SECONDS = 180


def _table_prefix(model) -> str:
    """Counts for both tables share one namespace; see filters_key."""
    return f"{model.__tablename__}:"


async def _hot(redis: Redis, entry: CacheEntry, model) -> list[AuctionFilters]:
    return [f for f in DEFAULT_FILTERS if await entry.exists(redis, filters_key(model, f))]


async def refresh_ended(db: AsyncSession, redis: Redis) -> None:
    """Call after a sync that wrote ended_auctions rows."""
    hot_counts = await _hot(redis, AUCTION_COUNT, EndedAuction)

    await AUCTION_COUNT.invalidate_all(redis, _table_prefix(EndedAuction))
    await TOP_SALES.invalidate_all(redis)

    for filters in hot_counts:
        await count_auctions(db, redis, EndedAuction, filters, build_where(EndedAuction, filters))


async def refresh_active(db: AsyncSession, redis: Redis) -> None:
    """Call after a sync that added or removed active_auctions rows."""
    hot_counts = await _hot(redis, AUCTION_COUNT, ActiveAuction)

    await AUCTION_COUNT.invalidate_all(redis, _table_prefix(ActiveAuction))

    for filters in hot_counts:
        await count_auctions(db, redis, ActiveAuction, filters, build_where(ActiveAuction, filters))


async def warm_stats(db: AsyncSession, redis: Redis) -> int:
    """Rebuild each WARM_STATS entry that is missing or about to expire.

    Returns how many were rebuilt. Each costs 1.5-2.3s of MySQL on prod data, so
    this runs ~once per entry per TTL rather than once per sale.
    """
    rebuilt = 0
    for filters in WARM_STATS:
        key = filters_key(EndedAuction, filters)
        if await AUCTION_STATS.ttl_remaining(redis, key) > REWARM_MARGIN_SECONDS:
            continue
        await rebuild_auction_stats(db, filters, redis)
        rebuilt += 1
    return rebuilt


async def invalidate_all(redis: Redis) -> None:
    """Every auction cache entry, for writers outside the sync loop."""
    await AUCTION_STATS.invalidate_all(redis)
    await AUCTION_COUNT.invalidate_all(redis)
    await TOP_SALES.invalidate_all(redis)
