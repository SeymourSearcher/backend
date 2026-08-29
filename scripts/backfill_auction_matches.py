"""Populate match columns on auction rows written before migration a1b2c3d4e5f6.

Run once after upgrading, and again after any change to static/data.json or to
TIER_THRESHOLDS:

    python -m scripts.backfill_auction_matches          # only unpopulated rows
    python -m scripts.backfill_auction_matches --all    # rewrite every row

Matches depend only on hex_color, so this resolves each distinct colour once,
stages the results in a temporary table and applies them with a single joined
UPDATE per table. Seymour colours are near-unique (~160k distinct across ~170k
rows), so a per-colour UPDATE would cost one network round trip per row -- fine
locally, hours over an SSH tunnel.

Safe to interrupt and re-run: without --all only rows still missing a match are
touched.
"""
import argparse
import asyncio
import logging
import sys

from sqlalchemy import text

from app.database import engine
from app.dependencies import get_redis
from app.services import auction_cache
from app.services.matching import match_columns

logger = logging.getLogger("backfill")

INSERT_CHUNK = 5000

MATCH_FIELDS = [
    "target_hex_keys",
    "delta_e_keys",
    "tier_keys",
    "target_hex_all",
    "delta_e_all",
    "tier_all",
]

TMP_TABLE = "hex_match_backfill"


async def _stage_matches(conn, hex_colors: list[str]) -> None:
    """Resolve every colour and load it into the temporary staging table."""
    await conn.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {TMP_TABLE}"))
    await conn.execute(text(f"""
        CREATE TEMPORARY TABLE {TMP_TABLE} (
            hex_color VARCHAR(6) NOT NULL PRIMARY KEY,
            target_hex_keys VARCHAR(6), delta_e_keys FLOAT, tier_keys VARCHAR(2),
            target_hex_all  VARCHAR(6), delta_e_all  FLOAT, tier_all  VARCHAR(2)
        )
    """))

    matches = match_columns(hex_colors)
    columns = ", ".join(["hex_color", *MATCH_FIELDS])
    placeholders = ", ".join(f":{name}" for name in ["hex_color", *MATCH_FIELDS])
    insert = text(f"INSERT INTO {TMP_TABLE} ({columns}) VALUES ({placeholders})")

    # Keyed on the stored value, not the uppercased one, so the join still
    # matches if any legacy row was written lowercase.
    rows = [{"hex_color": h, **matches[h.upper()]} for h in hex_colors]
    for start in range(0, len(rows), INSERT_CHUNK):
        await conn.execute(insert, rows[start:start + INSERT_CHUNK])
        logger.info("staged %d/%d colours", min(start + INSERT_CHUNK, len(rows)), len(rows))


async def backfill_table(conn, table: str, rewrite_all: bool) -> int:
    pending = "" if rewrite_all else " WHERE delta_e_all IS NULL"
    hex_colors = list(
        (await conn.execute(text(f"SELECT DISTINCT hex_color FROM {table}{pending}"))).scalars()
    )
    if not hex_colors:
        logger.info("%s: nothing to do", table)
        return 0

    logger.info("%s: %d distinct colours to resolve", table, len(hex_colors))
    await _stage_matches(conn, hex_colors)

    assignments = ", ".join(f"a.{name} = t.{name}" for name in MATCH_FIELDS)
    where = "" if rewrite_all else " WHERE a.delta_e_all IS NULL"
    result = await conn.execute(text(
        f"UPDATE {table} a JOIN {TMP_TABLE} t ON a.hex_color = t.hex_color"
        f" SET {assignments}{where}"
    ))
    await conn.commit()

    logger.info("%s: %d rows updated", table, result.rowcount)
    return result.rowcount


async def main(rewrite_all: bool) -> None:
    updated = 0
    async with engine.connect() as conn:
        for table in ("active_auctions", "ended_auctions"):
            updated += await backfill_table(conn, table, rewrite_all)
    await engine.dispose()

    if updated:
        # The auction caches are dropped by the sync loop, which has no idea
        # this ran. Without this they hold pre-backfill tier counts for a full
        # TTL. See app/services/auction_cache.py.
        await auction_cache.invalidate_all(await get_redis())
        logger.info("auction caches invalidated")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="rewrite every row, not just unpopulated ones")
    asyncio.run(main(parser.parse_args().all))
