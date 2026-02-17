import asyncio
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from redis.asyncio import Redis

from app.dependencies import get_db, get_redis, get_current_user
from app.services.stats import get_global_stats, get_stats, get_cached_or_compute_custom_sets, _schedule_custom_sets_background, sum_rankings_stats
from app.services.cache import get_cached_custom_sets
from app.services.cache import (
    get_cached_user_stats, set_cached_user_stats,
    get_cached_rankings, set_cached_rankings,
    set_cached_global_stats
)
from app.models import User, Seymour

router = APIRouter()


@router.get("/")
async def global_stats(
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await get_global_stats(redis)


@router.get("/me")
async def user_stats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    cached = await get_cached_user_stats(redis, user.id)
    if cached:
        return cached
    
    result = await db.execute(select(Seymour).where(Seymour.user_id == user.id))
    seymours = result.scalars().all()
    seymour_list = [(s.hex_color, s.item_name) for s in seymours]
    
    cached_custom = await get_cached_custom_sets(redis, seymour_list)
    if cached_custom is not None:
        stats = get_stats(seymour_list, custom_sets=cached_custom)
        await set_cached_user_stats(redis, user.id, stats)
    else:
        _schedule_custom_sets_background(redis, seymour_list, user_id=user.id)
        stats = get_stats(seymour_list, custom_sets=[])
    
    return stats


def _compute_user_ranking(user_data: dict, seymour_list: list, custom_sets: list | None = None) -> dict:
    stats = get_stats(seymour_list, custom_sets=custom_sets)
    return {**user_data, **stats}


@router.get("/rankings")
async def get_rankings(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    cached = await get_cached_rankings(redis)
    if cached:
        return cached
    
    result = await db.execute(
        select(User).options(selectinload(User.seymours)).where(User.seymours.any())
    )
    users = result.scalars().all()
    
    user_data_list = [
        (
            {"discord_id": u.discord_id, "username": u.username, "avatar_hash": u.avatar_hash},
            [(s.hex_color, s.item_name) for s in u.seymours]
        )
        for u in users
    ]
    
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor() as executor:
        tasks = [
            loop.run_in_executor(executor, partial(_compute_user_ranking, user_data, seymour_list))
            for user_data, seymour_list in user_data_list
        ]
        rankings = list(await asyncio.gather(*tasks))
    
    await set_cached_rankings(redis, rankings)
    
    # Also update global stats from rankings
    global_stats = sum_rankings_stats(rankings)
    await set_cached_global_stats(redis, global_stats)
    
    return rankings

