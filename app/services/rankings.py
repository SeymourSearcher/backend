import asyncio
from concurrent.futures import ProcessPoolExecutor
from functools import partial

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories.seymour_repo import SeymourRepository
from app.repositories.user_repo import UserRepository
from app.services.cache import (
    get_cached_rankings,
    get_cached_user_stats,
    set_cached_global_stats,
    set_cached_rankings,
    set_cached_user_stats,
)
from app.services.stats import get_stats, sum_rankings_stats


def _compute_user_ranking(user_data: dict, seymour_list: list) -> dict:
    stats = get_stats(seymour_list)
    return {**user_data, **stats}


async def user_stats(db: AsyncSession, redis: Redis, user: User) -> dict:
    cached = await get_cached_user_stats(redis, user.id)
    if cached:
        return cached

    seymours = await SeymourRepository(db).list_for_user(user.id)
    stats = get_stats([(s.hex_color, s.item_name) for s in seymours])
    await set_cached_user_stats(redis, user.id, stats)

    return stats


async def compute_and_cache_rankings(db: AsyncSession, redis: Redis) -> list[dict]:
    """Compute rankings from database and cache them along with global stats."""
    users = await UserRepository(db).list_with_seymours()

    user_data_list = [
        (
            {"discord_id": str(u.discord_id), "username": u.username, "avatar_hash": u.avatar_hash},
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
    await set_cached_global_stats(redis, sum_rankings_stats(rankings))

    return rankings


async def rankings(db: AsyncSession, redis: Redis) -> list[dict]:
    cached = await get_cached_rankings(redis)
    if cached:
        return cached
    return await compute_and_cache_rankings(db, redis)
