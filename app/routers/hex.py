from typing import Annotated
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_redis
from app.schemas.hex import HexInfoResponse, HexOwnersResponse
from app.services.hex_info import get_hex_info, get_hex_owners

router = APIRouter()


@router.get("/{hex_code}", response_model=HexInfoResponse)
async def hex_info(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    include_dyes: bool = False
):
    return await get_hex_info(db, redis, hex_code, include_dyes)


@router.get("/{hex_code}/owners", response_model=HexOwnersResponse)
async def hex_owners(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    limit: int = Query(5, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return await get_hex_owners(db, redis, hex_code, limit, offset)
