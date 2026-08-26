"""Resolve usernames for auction participants that have no players row yet.

Run once alongside the deploy that moves username lookups off the read path
(migration c4f9a2b81d7e). Until it finishes, auctions whose seller or buyer has
never been resolved render without a name:

    python -m scripts.backfill_player_names
    python -m scripts.backfill_player_names --limit 200   # trial run

The refresh worker only scans a window of recent auctions, so it will never
reach the historical backlog on its own -- that is what this script is for.

Ordered by most recent appearance so the pages people actually load go correct
first. Safe to interrupt and re-run: each batch commits, and the candidate query
only ever returns uuids that still have no row.
"""
import argparse
import asyncio
import logging
import sys
import time

from sqlalchemy import text

from app.database import async_session, engine
from app.services.mojang import close_client
from app.services.player import resolve_names

logger = logging.getLogger("backfill-names")

# resolve_names fans out a whole batch at once and the httpx pool caps real
# concurrency at MAX_CONNECTIONS, so this only sets the commit granularity.
BATCH_SIZE = 100

# Hypixel timestamps are milliseconds; older rows were stored as seconds. Left
# unnormalised the two scales interleave and the ordering is meaningless.
MS_CUTOFF = 9_999_999_999


def _seconds(column: str) -> str:
    return f"CASE WHEN {column} > {MS_CUTOFF} THEN {column} / 1000 ELSE {column} END"


CANDIDATES = text(f"""
    SELECT x.u AS uuid
    FROM (
        SELECT seller_uuid AS u, {_seconds('timestamp')} AS t FROM ended_auctions
        UNION ALL
        SELECT buyer_uuid,       {_seconds('timestamp')}       FROM ended_auctions
        UNION ALL
        SELECT seller_uuid,      {_seconds('start_time')}      FROM active_auctions
        UNION ALL
        SELECT bidder_uuid,      {_seconds('timestamp')}       FROM bids
    ) x
    LEFT JOIN players p ON p.uuid = x.u
    WHERE p.uuid IS NULL AND x.u IS NOT NULL AND x.u <> ''
    GROUP BY x.u
    ORDER BY MAX(x.t) DESC
""")


async def find_candidates(db, limit: int | None) -> list[str]:
    logger.info("scanning auction tables for unresolved uuids...")
    started = time.perf_counter()
    uuids = list((await db.execute(CANDIDATES)).scalars())
    logger.info("%d unresolved uuids (%.1fs)", len(uuids), time.perf_counter() - started)
    return uuids[:limit] if limit else uuids


async def backfill(limit: int | None, batch_size: int) -> None:
    async with async_session() as db:
        uuids = await find_candidates(db, limit)
        if not uuids:
            logger.info("nothing to do")
            return

        resolved = 0
        started = time.perf_counter()

        for start in range(0, len(uuids), batch_size):
            batch = uuids[start:start + batch_size]
            resolved += len(await resolve_names(db, batch))

            done = start + len(batch)
            rate = done / max(time.perf_counter() - started, 1e-9)
            logger.info(
                "%d/%d attempted, %d resolved, %.0f/s, ~%.0fs left",
                done, len(uuids), resolved, rate, (len(uuids) - done) / max(rate, 1e-9),
            )

        missing = len(uuids) - resolved
        logger.info("done: %d resolved, %d unresolvable", resolved, missing)
        if missing:
            logger.info("unresolvable uuids are usually deleted accounts; re-running retries them")


async def main(limit: int | None, batch_size: int) -> None:
    try:
        await backfill(limit, batch_size)
    finally:
        await close_client()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )
    # One INFO line per request would be ~29k lines of noise on a full run.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="stop after this many uuids (trial runs)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.batch_size))
