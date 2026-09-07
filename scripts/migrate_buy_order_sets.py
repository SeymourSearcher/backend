"""Rename buy_order_set_criteria.set_name to match a checklist sync.

`sets` keys are stored on buy orders as plain strings, so renaming a set in
app/static/data.json orphans every order pointing at the old name --
is_valid_set() goes false and the order silently stops matching. Run this once
after scripts/sync_checklist.py renames anything:

    python -m scripts.migrate_buy_order_sets --dry-run
    python -m scripts.migrate_buy_order_sets

Orders whose set upstream deleted outright cannot be rewritten; they are listed
so they can be cancelled or repointed by hand.
"""
import argparse
import asyncio
import json
import logging
import os
import sys

from sqlalchemy import text

from app.database import engine

logger = logging.getLogger("migrate-sets")

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
RENAMES_JSON = os.path.join(SCRIPTS_DIR, "data_item_renames.json")
DATA_JSON = os.path.join(SCRIPTS_DIR, "..", "app", "static", "data.json")


def _plan(stored: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map each stored set name to its current one; report the unresolvable."""
    with open(RENAMES_JSON) as f:
        renames = json.load(f)
    with open(DATA_JSON) as f:
        valid = set(json.load(f)["sets"])

    updates, orphans = {}, []
    for name in stored:
        if name in valid:
            continue
        new = renames.get(name, name)
        if new in valid:
            updates[name] = new
        else:
            orphans.append(name)
    return updates, orphans


async def main(dry_run: bool) -> int:
    async with engine.connect() as conn:
        stored = list((await conn.execute(
            text("SELECT DISTINCT set_name FROM buy_order_set_criteria")
        )).scalars())
        updates, orphans = _plan(stored)

        for old, new in updates.items():
            result = await conn.execute(
                text("SELECT COUNT(*) FROM buy_order_set_criteria WHERE set_name = :old"),
                {"old": old},
            )
            logger.info("%s -> %s (%d orders)%s", old, new, result.scalar(),
                        " [dry run]" if dry_run else "")
            if not dry_run:
                await conn.execute(
                    text("UPDATE buy_order_set_criteria SET set_name = :new WHERE set_name = :old"),
                    {"old": old, "new": new},
                )
        if not dry_run:
            await conn.commit()

    await engine.dispose()

    if not updates:
        logger.info("no set names to rewrite")
    if orphans:
        logger.warning("no current set for %d stored name(s) -- cancel or repoint by hand: %s",
                       len(orphans), ", ".join(sorted(orphans)))
    return 1 if orphans else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report the plan without writing")
    sys.exit(asyncio.run(main(parser.parse_args().dry_run)))
