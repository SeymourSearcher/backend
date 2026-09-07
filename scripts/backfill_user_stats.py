"""Populate user_stats for users who uploaded before migration a8c1f4e60b92.

Run once after upgrading, and again after any change to the stat definitions in
app/services/stats.py (the tallies are a snapshot, not a view):

    python -m scripts.backfill_user_stats            # only users with no row
    python -m scripts.backfill_user_stats --all      # recompute everyone

This also rebuilds user_custom_sets, since refresh_user_stats writes both. Use
--all to populate them after migration b3e7c9a15d84: the default selects on a
missing user_stats row, which a user tallied before that migration still has.

One user at a time, because that is also how the live path recomputes them: a
user's stats depend only on their own Seymours. Roughly 600ms for all 32 users
on prod before the custom-set search was added, dominated by the largest
collection either way.
"""
import argparse
import asyncio
import logging
import sys
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import engine
from app.models import Seymour, User, UserStats
from app.services.rankings import refresh_user_stats

logger = logging.getLogger("backfill")


async def _users_to_do(db, everyone: bool) -> list[int]:
    """Users owning at least one Seymour, since only they can have stats."""
    query = select(User.id).where(select(Seymour.id).where(Seymour.user_id == User.id).exists())
    if not everyone:
        query = query.where(~select(UserStats.user_id).where(UserStats.user_id == User.id).exists())
    return list((await db.scalars(query.order_by(User.id))).all())


async def backfill(everyone: bool) -> None:
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        user_ids = await _users_to_do(db, everyone)
        logger.info("%d users to compute", len(user_ids))

        started = time.perf_counter()
        for done, user_id in enumerate(user_ids, 1):
            at = time.perf_counter()
            stats = await refresh_user_stats(db, user_id)
            logger.info(
                "%d/%d user %d: %d seymours in %.0fms",
                done, len(user_ids), user_id, stats["total_seymours"],
                (time.perf_counter() - at) * 1000,
            )

        logger.info("done in %.1fs", time.perf_counter() - started)


async def main(everyone: bool) -> None:
    try:
        await backfill(everyone)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="recompute users that already have a row")
    args = parser.parse_args()
    asyncio.run(main(args.all))
