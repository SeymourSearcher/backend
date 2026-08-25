from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.dependencies import get_db, get_redis, get_current_user
from app.schemas.stats import RankingResponse, StatsResponse
from app.services.rankings import rankings, user_stats
from app.services.stats import get_global_stats
from app.models import User

router = APIRouter()


@router.get("/", response_model=StatsResponse)
async def global_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await get_global_stats(redis, db)


@router.get("/me", response_model=StatsResponse)
async def get_user_stats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await user_stats(db, redis, user)


@router.get("/rankings", response_model=list[RankingResponse])
async def get_rankings(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await rankings(db, redis)
