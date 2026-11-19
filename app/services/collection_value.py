"""What a collection is worth, broken down by the /items filter chips.

Each item is priced from the median sale of its nearest catalog colour at its
own tier -- the same basis, and the same numbers, as the hex page's estimate, so
the two can never disagree about one colour.

Piece-agnostic on purpose: a helmet near Cropie is priced from every Cropie sale
in its tier rather than helmet sales alone. That is roughly four times the
sample, and far fewer items left with nothing to compare against.

The chips overlap -- one Seymour can be a Star *and* T1 *and* part of a set --
so the per-category figures deliberately do not sum to `total`. The UI says so.
"""
import asyncio

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cache import COLLECTION_VALUE, custom_sets_hash
from app.services.hex_prices import median_prices_for
from app.services.stats import CollectionBreakdown, categorise_collection

# No "custom": find_custom_sets grows superlinearly and is refused above
# MAX_INVENTORY, so it is the one category that could fail to produce a number
# for exactly the collections whose owners most want one.
CATEGORIES = ("stars", "words", "dupes", "t1", "t2", "t3", "sets", "dyes")


def _summarise(breakdown: CollectionBreakdown, prices: dict) -> dict:
    """Fold priced rows into the headline and the per-chip totals."""
    categories = dict.fromkeys(CATEGORIES, 0)
    total = 0
    priced = 0

    for row in breakdown.rows:
        estimate = prices.get((row.target_hex, row.tier))
        if estimate is None or estimate["median"] is None:
            continue

        value = round(estimate["median"])
        priced += 1
        total += value

        if row.star:
            categories["stars"] += value
        if row.word:
            categories["words"] += value
        # Excess copies only, matching what the Dupes chip counts.
        if row.excess_dupe:
            categories["dupes"] += value
        if row.tier:
            categories[row.tier] += value
        # Tagged on one copy per colour, so a set is never priced twice.
        if row.in_armor_set:
            categories["sets"] += value
        if row.in_dye_set:
            categories["dyes"] += value

    return {
        "total": total,
        "priced": priced,
        "unpriced": len(breakdown.rows) - priced,
        "categories": categories,
    }


async def value_collection(
    db: AsyncSession,
    redis: Redis,
    seymour_list: list[tuple[str, int]],
    include_dyes: bool = False,
) -> dict:
    """Cached on the auction TTL, which bounds how stale the prices can be.

    Keyed by the collection's contents rather than by user, so two people
    holding the same items share the entry -- and so re-uploading an unchanged
    file is a hit.
    """
    key = custom_sets_hash(seymour_list)

    cached = await COLLECTION_VALUE.get(redis, key, include_dyes)
    if cached is not None:
        return cached

    breakdown = await asyncio.to_thread(categorise_collection, seymour_list)

    pairs = sorted({
        (row.target_hex, row.tier)
        for row in breakdown.rows
        if row.target_hex is not None and row.tier is not None
    })
    prices = await median_prices_for(db, pairs, include_dyes)

    value = _summarise(breakdown, prices)
    await COLLECTION_VALUE.set(redis, value, key, include_dyes)
    return value
