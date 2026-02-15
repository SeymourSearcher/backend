from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from redis.asyncio import Redis

from app.dependencies import get_db, get_redis, get_current_user
from app.services.stats import get_global_stats, get_stats
from app.services.compute import seymour_converter
from app.models import User, Seymour

router = APIRouter()


@router.get("/")
async def global_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await get_global_stats(db, redis)


@router.get("/me")
async def user_stats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(select(Seymour).where(Seymour.user_id == user.id))
    seymours = result.scalars().all()
    seymour_list = [(s.hex_color, s.item_name) for s in seymours]
    return get_stats(seymour_list)

