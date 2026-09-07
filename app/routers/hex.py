from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_redis
from app.pieces import parse_pieces
from app.schemas.hex import HexAuctionStatsResponse, HexInfoResponse, HexOwnersResponse
from app.services.hex_info import get_hex_auction_stats, get_hex_info, get_hex_owners

router = APIRouter()

PiecesParam = Annotated[str | None, Query(description="Comma-separated piece indices, 0-3")]


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
    tier: Literal["t1", "t2", "t3", "all"] | None = Query(None),
):
    return await get_hex_auction_stats(
        db, redis, hex_code, include_dyes, parse_pieces(pieces), tier
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
