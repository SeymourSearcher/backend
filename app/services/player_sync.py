"""Keeps the players table populated so the read path never has to.

Unresolved uuids are worked before stale ones: a missing name renders as blank
on the site, whereas a month-old name is merely slightly wrong.

The two have separate budgets because they cost different amounts. An
unresolved uuid needs a whole request; a stale one is revalidated ten to a
request, so its budget can be an order of magnitude larger for the same
upstream load. They also hit different hosts, so neither crowds the other.

Budgets are what pace upstream calls -- there is no TTL jitter because there is
no herd to spread. Taking the oldest N each cycle is constant rate by
construction, and a backlog stretches the sweep instead of spiking.
"""
import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.database import async_session
from app.repositories.player_repo import PlayerRepository
from app.services.player import REFRESH_AFTER, resolve_names, revalidate_names

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 60

# One request each. Steady state is a trickle; this is headroom for a backlog.
UNRESOLVED_BATCH = 50

# A tenth of a request each. 25-day refresh over ~30k rows needs about one per
# cycle, so this is almost entirely catch-up capacity.
STALE_BATCH = 200


async def refresh_players(db: AsyncSession) -> tuple[int, int, int]:
    repo = PlayerRepository(db)

    unresolved = await repo.unresolved_uuids(UNRESOLVED_BATCH)
    if unresolved:
        await resolve_names(db, unresolved)

    stale = await repo.stale_players(STALE_BATCH, utcnow() - REFRESH_AFTER)
    confirmed, changed = await revalidate_names(db, stale)

    return len(unresolved), confirmed, changed


async def run_player_refresh() -> tuple[int, int, int]:
    async with async_session() as db:
        try:
            return await refresh_players(db)
        except Exception:
            await db.rollback()
            raise


async def player_refresh_loop():
    logger.info(f"Starting player refresh loop (interval: {REFRESH_INTERVAL}s)")
    while True:
        try:
            new, confirmed, changed = await run_player_refresh()
            if new or confirmed or changed:
                logger.info(
                    f"Player refresh: {new} newly resolved, "
                    f"{confirmed} confirmed, {changed} renamed."
                )
        except Exception as e:
            logger.error(f"Player refresh loop error: {e}")
        await asyncio.sleep(REFRESH_INTERVAL)
