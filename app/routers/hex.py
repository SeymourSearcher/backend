from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_redis
from app.pieces import parse_pieces
from app.rate_limit import RateLimit, limiter
from app.schemas.hex import (
    HexAuctionStatsResponse,
    HexInfoResponse,
    HexOwnersResponse,
    HexSalesResponse,
)
from app.services.hex_info import (
    get_hex_auction_stats,
    get_hex_info,
    get_hex_owners,
    get_hex_sales,
)

PiecesParam = Annotated[str | None, Query(description="Comma-separated piece indices, 0-3")]
TierParam = Annotated[Literal["t1", "t2", "t3", "all"] | None, Query()]

# Tighter than the global ceiling because the cache behind these is keyed by the
# hex code, of which there are 16.7M: a caller walking them misses every time,
# and each miss is several full passes over the owner table (~57ms per million
# owned rows). Four requests per page view, so this is ~15 hex pages a minute.
HEX_LIMIT = RateLimit("hex", 60, 60)

router = APIRouter(dependencies=[Depends(limiter(HEX_LIMIT))])


@router.get("/{hex_code}", response_model=HexInfoResponse)
async def hex_info(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    include_dyes: bool = False,
    pieces: PiecesParam = None,
):
    return await get_hex_info(db, redis, hex_code, include_dyes, parse_pieces(pieces))


@router.get("/{hex_code}/auction-stats", response_model=HexAuctionStatsResponse)
async def hex_auction_stats(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    include_dyes: bool = False,
    pieces: PiecesParam = None,
    tier: TierParam = None,
):
    return await get_hex_auction_stats(
        db, redis, hex_code, include_dyes, parse_pieces(pieces), tier
    )


@router.get("/{hex_code}/sales", response_model=HexSalesResponse)
async def hex_sales(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    include_dyes: bool = False,
    pieces: PiecesParam = None,
    tier: TierParam = None,
    limit: int = Query(8, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    return await get_hex_sales(
        db, redis, hex_code, include_dyes, parse_pieces(pieces), tier, limit, offset
    )


@router.get("/{hex_code}/owners", response_model=HexOwnersResponse)
async def hex_owners(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    limit: int = Query(5, ge=1, le=100),
    offset: int = Query(0, ge=0),
    pieces: PiecesParam = None,
):
    return await get_hex_owners(db, redis, hex_code, limit, offset, parse_pieces(pieces))
