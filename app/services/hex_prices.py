"""Median sale price for a catalog colour at a given tier.

Deliberately not compute_auction_stats: that runs four passes and builds a
histogram plus an hourly breakdown, and the hex page wants two numbers. Both
queries here ride ix_ended_target_all_price / ix_ended_target_keys_price, so
they are covered by the index and never touch a row.

Priced by (target_hex, tier) rather than by the raw hex_color: an arbitrary
colour has essentially no exact sales among the ended auctions, while its
nearest catalog colour at the same tier is what a buyer is actually comparing
it against.
"""
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EndedAuction
from app.services.auction_queries import match_columns_for
from app.services.cache import HEX_PRICES


def _where(target_hex: str, tier: str, include_dyes: bool, pieces: list[int] | None):
    target_col, _, tier_col = match_columns_for(EndedAuction, include_dyes)
    conditions = [target_col == target_hex, tier_col == tier]
    if pieces:
        conditions.append(EndedAuction.piece.in_(pieces))
    return conditions


async def _median_and_count(
    db: AsyncSession, target_hex: str, tier: str, include_dyes: bool, pieces: list[int] | None
) -> dict:
    conditions = _where(target_hex, tier, include_dyes, pieces)

    sales = await db.scalar(select(func.count()).select_from(EndedAuction).where(*conditions))
    if not sales:
        return {"median": None, "sales": 0}

    # OFFSET into the price-ordered index beats ROW_NUMBER() here because the
    # filter is already the index prefix -- there is nothing left to sort.
    median = await db.scalar(
        select(EndedAuction.price)
        .where(*conditions)
        .order_by(EndedAuction.price)
        .offset(sales // 2)
        .limit(1)
    )
    return {"median": float(median), "sales": sales}


async def price_estimates(
    db: AsyncSession,
    redis: Redis,
    targets: list[tuple[str, str | None]],
    include_dyes: bool,
    pieces: list[int] | None,
) -> list[dict]:
    """One {median, sales} per (target_hex, tier), in the order given.

    A target whose tier is None -- the colour is further than the last tier --
    is unpriceable, since there is no comparable band of sales to draw on.
    """
    key = (
        f"{include_dyes}:{','.join(map(str, pieces or []))}:"
        + "|".join(f"{t}@{tier}" for t, tier in targets)
    )
    cached = await HEX_PRICES.get(redis, key)
    if cached is not None:
        return cached

    estimates = [
        {"median": None, "sales": 0}
        if tier is None
        else await _median_and_count(db, target_hex, tier, include_dyes, pieces)
        for target_hex, tier in targets
    ]
    await HEX_PRICES.set(redis, estimates, key)
    return estimates
