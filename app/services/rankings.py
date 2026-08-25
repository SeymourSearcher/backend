import asyncio

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


def _compute_rankings(user_data_list: list[tuple[dict, list]]) -> list[dict]:
    """Deliberately serial, and deliberately not a ProcessPoolExecutor.

    Each worker would re-import app.services.compute (~2.4s and ~200MB for the
    LAB table) and every Seymour list would be pickled across the boundary; at
    every measured size that cost more than the work itself. get_stats already
    fans out over threads internally, so a single to_thread call keeps the event
    loop free without paying for processes.
    """
    return [_compute_user_ranking(user_data, seymours) for user_data, seymours in user_data_list]


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

    rankings = await asyncio.to_thread(_compute_rankings, user_data_list)

    await set_cached_rankings(redis, rankings)

    # Also update global stats from rankings
    await set_cached_global_stats(redis, sum_rankings_stats(rankings))

    return rankings


async def rankings(db: AsyncSession, redis: Redis) -> list[dict]:
    cached = await get_cached_rankings(redis)
    if cached:
        return cached
    return await compute_and_cache_rankings(db, redis)
