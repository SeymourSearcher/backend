from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_optional_user, get_redis, public_cache
from app.models import User
from app.pieces import parse_pieces
from app.rate_limit import RateLimit, limiter
from app.schemas.hex_search import (
    HexSearchResponse,
    PatternAuctionStatsResponse,
    PatternSalesResponse,
)
from app.services.hex_search import MAX_OFFSET, search_hex_pattern
from app.services.pattern_auctions import get_pattern_auction_stats, get_pattern_sales

PatternParam = Annotated[
    str, Query(description="Six-slot pattern: 0-9A-F literal, ? wildcard, G-Z variable")
]
PiecesParam = Annotated[str | None, Query(description="Comma-separated piece indices, 0-3")]
TierParam = Annotated[Literal["t1", "t2", "t3", "all"] | None, Query()]

# Sharper than HEX_LIMIT. The unbounded-cache-key argument there applies to the
# ~2.8e9 patterns too, but the cost is worse: a five-free-slot pattern gathers ΔE
# for a million colours per request, and unlike the hex pages there is nothing
# worth caching behind the search itself -- the key would vary in seven
# dimensions at once. The two auction routes are cheaper SQL and get their own
# bucket, since every view of the page calls both alongside the search.
HEX_SEARCH_LIMIT = RateLimit("hex_search", 30, 60)
PATTERN_AUCTIONS_LIMIT = RateLimit("hex_search:auctions", 60, 60)

router = APIRouter()
_search_limited = [Depends(limiter(HEX_SEARCH_LIMIT))]
# Public: unlike the search itself, these never read the caller.
_auctions_limited = [Depends(limiter(PATTERN_AUCTIONS_LIMIT)), public_cache()]


@router.get("", response_model=HexSearchResponse, dependencies=_search_limited)
async def hex_search(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    user: Annotated[User | None, Depends(get_optional_user)],
    # A query param, not a path segment: '?' is not path-safe.
    p: PatternParam,
    include_dyes: bool = False,
    # Ignored for an anonymous caller, who has no holdings to narrow to.
    mine_only: bool = False,
    tier: TierParam = None,
    pieces: PiecesParam = None,
    limit: int = Query(10, ge=1, le=100),
    # Deep paging answers nothing, and the offset is what widens the tie window
    # the page selection sorts.
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    return await search_hex_pattern(
        db, redis, p, include_dyes, mine_only, tier, parse_pieces(pieces),
        limit, offset, user.id if user else None,
    )


# The two routes below take the search's own filters rather than their own, so
# the figures always describe the slice the result grid is showing. mine_only is
# not among them: an ended auction belongs to whoever bought it.
@router.get(
    "/auction-stats", response_model=PatternAuctionStatsResponse, dependencies=_auctions_limited
)
async def pattern_auction_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    p: PatternParam,
    include_dyes: bool = False,
    tier: TierParam = None,
    pieces: PiecesParam = None,
):
    return await get_pattern_auction_stats(db, redis, p, include_dyes, tier, parse_pieces(pieces))


@router.get("/sales", response_model=PatternSalesResponse, dependencies=_auctions_limited)
async def pattern_sales(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    p: PatternParam,
    include_dyes: bool = False,
    tier: TierParam = None,
    pieces: PiecesParam = None,
    limit: int = Query(8, ge=1, le=50),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    return await get_pattern_sales(
        db, redis, p, include_dyes, tier, parse_pieces(pieces), limit, offset
    )
