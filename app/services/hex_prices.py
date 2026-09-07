"""Median sale price for a catalog colour at a given tier.

Deliberately not compute_auction_stats: that runs four passes and builds a
histogram plus an hourly breakdown, and the hex page wants two numbers. The
query here rides ix_ended_target_all_price / ix_ended_target_keys_price.

Priced by (target_hex, tier) rather than by the raw hex_color: an arbitrary
colour has essentially no exact sales among the ended auctions, while its
nearest catalog colour at the same tier is what a buyer is actually comparing
it against.
"""
from redis.asyncio import Redis
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EndedAuction
from app.services.auction_queries import match_columns_for
from app.services.cache import HEX_PRICES

UNPRICED = {"median": None, "sales": 0}


async def median_prices_for(
    db: AsyncSession,
    pairs: list[tuple[str, str | None]],
    include_dyes: bool,
    pieces: list[int] | None = None,
) -> dict[tuple[str, str], dict]:
    """{median, sales} per (target_hex, tier), for many pairs in one query.

    Pairs with no sales are absent from the result rather than present as zero,
    so a caller distinguishes "nothing comparable sold" from a price of nothing.
    A pair whose tier is None is dropped up front: past the last tier there is no
    band of comparable sales to draw on.

    Takes the *lower* median -- the single row at index sales // 2 of the
    price-ordered partition, hence rn = sales // 2 + 1 one-indexed. Note
    auction_stats._median_from averages the middle two instead; the two are not
    interchangeable, and using that one here would make /items and the hex page
    quote different numbers for the same colour.
    """
    wanted = [(target, tier) for target, tier in pairs if tier is not None]
    if not wanted:
        return {}

    target_col, _, tier_col = match_columns_for(EndedAuction, include_dyes)
    partition = (target_col, tier_col)

    conditions = [tuple_(target_col, tier_col).in_(wanted)]
    if pieces:
        conditions.append(EndedAuction.piece.in_(pieces))

    ranked = (
        select(
            target_col.label("target_hex"),
            tier_col.label("tier"),
            EndedAuction.price,
            func.row_number().over(partition_by=partition, order_by=EndedAuction.price).label("rn"),
            func.count().over(partition_by=partition).label("sales"),
        )
        .where(*conditions)
        .cte("ranked")
    )

    rows = await db.execute(
        select(ranked.c.target_hex, ranked.c.tier, ranked.c.sales, ranked.c.price)
        .where(ranked.c.rn == func.floor(ranked.c.sales / 2) + 1)
    )

    return {
        (target_hex, tier): {"median": float(price), "sales": sales}
        for target_hex, tier, sales, price in rows.all()
    }


async def price_estimates(
    db: AsyncSession,
    redis: Redis,
    targets: list[tuple[str, str | None]],
    include_dyes: bool,
    pieces: list[int] | None,
) -> list[dict]:
    """One {median, sales} per (target_hex, tier), in the order given."""
    key = (
        f"{include_dyes}:{','.join(map(str, pieces or []))}:"
        + "|".join(f"{t}@{tier}" for t, tier in targets)
    )
    cached = await HEX_PRICES.get(redis, key)
    if cached is not None:
        return cached

    prices = await median_prices_for(db, targets, include_dyes, pieces)
    estimates = [prices.get((target, tier), UNPRICED) for target, tier in targets]

    await HEX_PRICES.set(redis, estimates, key)
    return estimates
