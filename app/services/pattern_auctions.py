"""Ended-auction statistics and sales for a hex pattern.

Filtered on the auction's own colour, not on the catalog colour it was matched
to. The hex pages widen an exact colour to its target because one colour almost
never has sales of its own; a pattern already denotes a set, so widening it the
same way would price catalog colours the caller never asked about. The cost of
staying literal is that a pattern with no free slots is one exact colour and
will usually find nothing.

Everything else -- the filters, the aggregation, the caching -- is the auction
machinery unchanged; see app/services/auction_queries.py.
"""
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EndedAuction
from app.schemas.auction import EndedAuctionResponse
from app.services.auction_queries import AuctionFilters, attach_names, list_auctions
from app.services.auction_stats import compute_auction_stats
from app.services.hex_pattern import HexPattern, parse_pattern


def _filters(
    pattern: HexPattern, include_dyes: bool, tier: str | None, pieces: list[int] | None
) -> AuctionFilters:
    return AuctionFilters(
        hex_pattern=pattern.raw,
        include_dyes=include_dyes,
        # "all" is the UI's every-band sentinel; the query layer spells that None.
        tier=tier if tier and tier != "all" else None,
        pieces=pieces,
    )


def _context(pattern: HexPattern, filters: AuctionFilters) -> dict:
    return {
        "pattern": pattern.raw,
        "pattern_total": pattern.total,
        "tier": filters.tier,
    }


async def get_pattern_auction_stats(
    db: AsyncSession,
    redis: Redis,
    raw_pattern: str,
    include_dyes: bool = False,
    tier: str | None = None,
    pieces: list[int] | None = None,
) -> dict:
    pattern = parse_pattern(raw_pattern)
    filters = _filters(pattern, include_dyes, tier, pieces)
    stats = await compute_auction_stats(db, filters, redis=redis)
    return {**_context(pattern, filters), **stats}


async def get_pattern_sales(
    db: AsyncSession,
    redis: Redis,
    raw_pattern: str,
    include_dyes: bool = False,
    tier: str | None = None,
    pieces: list[int] | None = None,
    limit: int = 8,
    offset: int = 0,
) -> dict:
    """The rows get_pattern_auction_stats aggregates, newest first."""
    pattern = parse_pattern(raw_pattern)
    filters = _filters(pattern, include_dyes, tier, pieces)

    items, total = await list_auctions(
        db, EndedAuction, EndedAuctionResponse, filters,
        "timestamp", "desc", offset, limit, redis=redis,
    )
    await attach_names(db, items, "seller_uuid", "buyer_uuid")

    return {**_context(pattern, filters), "total": total, "items": items}
