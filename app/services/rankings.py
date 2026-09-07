"""Persisted per-user stats, and the leaderboard read built on them.

A user's stats change only when their Seymours do, so they are recomputed on
write and stored. The read path is then a join over one row per user.

This replaced a Redis cache of the whole leaderboard. That cache was invalidated
by *any* user's upload, so one person uploading made the next visitor recompute
all 32 users -- 9.5s on prod. It also held the same numbers as the global-stats
cache under a different invalidation rule, so deleting a Seymour left the two
disagreeing for up to an hour.
"""
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.user_stats import STAT_KEYS
from app.repositories.seymour_repo import SeymourRepository
from app.repositories.user_stats_repo import UserStatsRepository
from app.services.custom_sets import refresh_user_custom_sets
from app.services.stats import get_stats

# The leaderboard is a top-N board, not a directory: the client renders this
# many rows, so shipping more is waste.
RANKING_LIMIT = 25

# get_stats fans out over threads internally, so one to_thread call keeps the
# event loop free. Deliberately not a ProcessPoolExecutor: each worker would
# re-import app.services.compute (~2.4s and ~200MB for the LAB table) and every
# Seymour list would be pickled across the boundary, which at every measured
# size cost more than the work itself.
async def _compute_stats(seymour_list: list[tuple[str, int]]) -> dict:
    return await asyncio.to_thread(get_stats, seymour_list)


def _as_dict(stats) -> dict:
    return {key: getattr(stats, key) for key in STAT_KEYS}


async def refresh_user_stats(db: AsyncSession, user_id: int) -> dict:
    """Recompute and store one user's stats and featured sets.

    Call after their Seymours change. Costs one user's compute -- 86ms for the
    largest collection on prod, 3ms for most -- rather than everyone's, plus the
    custom-set search, which is the heavier of the two.
    """
    seymours = await SeymourRepository(db).list_for_user(user_id)
    seymour_list = [(s.hex_color, s.item_name) for s in seymours]
    stats = await _compute_stats(seymour_list)

    repo = UserStatsRepository(db)
    if seymours:
        await repo.upsert(user_id, stats)
    else:
        # No row rather than a row of zeroes: the leaderboard has only ever
        # listed users holding at least one Seymour.
        await repo.delete_for_user(user_id)

    # Empty list included: this is also what clears the rows of a user who just
    # deleted their collection.
    await refresh_user_custom_sets(db, user_id, seymour_list)

    await db.commit()
    return stats


async def user_stats(db: AsyncSession, user: User) -> dict:
    stored = await UserStatsRepository(db).get(user.id)
    if stored is None:
        # Writes on a GET, but only for a user who has never been tallied --
        # one user's compute, and it self-heals rather than 404ing.
        return await refresh_user_stats(db, user.id)
    return _as_dict(stored)


async def rankings(db: AsyncSession, sort_key: str = "total_seymours") -> list[dict]:
    return [
        {
            "discord_id": str(user.discord_id),
            "username": user.username,
            "avatar_hash": user.avatar_hash,
            **_as_dict(stats),
        }
        for user, stats in await UserStatsRepository(db).list_ranked(sort_key, RANKING_LIMIT)
    ]


async def global_stats(db: AsyncSession) -> dict:
    """Summed in SQL over one row per user."""
    return await UserStatsRepository(db).totals()
