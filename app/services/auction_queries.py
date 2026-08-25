"""Query construction and serialisation for the auction tables.

Every filter here resolves to a SQL predicate over persisted columns, so
filtering, sorting and pagination all happen in the database. See
app/services/matching.py for why tier is a range predicate rather than a column.
"""
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, asc, desc, false, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActiveAuction
from app.services.matching import (
    compute_abs_diff,
    get_name_for_hex,
    get_set_target_hexes,
    get_tier,
    tier_bounds,
)


@dataclass(frozen=True)
class AuctionFilters:
    hex_color: str | None = None
    pieces: list[int] | None = None
    min_price: int | None = None
    max_price: int | None = None
    bin_filter: str | None = None
    tier: str | None = None
    set_name: str | None = None
    include_dyes: bool = True


def parse_pieces(pieces: str | None) -> list[int] | None:
    if not pieces:
        return None
    parsed = [int(p) for p in pieces.split(",") if p.strip().isdigit()]
    return parsed or None


def match_columns_for(model, include_dyes: bool):
    """Returns (target_hex, delta_e, tier) columns for the requested target set."""
    if include_dyes:
        return model.target_hex_all, model.delta_e_all, model.tier_all
    return model.target_hex_keys, model.delta_e_keys, model.tier_keys


def build_where(model, filters: AuctionFilters):
    target_col, _, tier_col = match_columns_for(model, filters.include_dyes)
    conditions = []

    if filters.hex_color:
        conditions.append(model.hex_color == filters.hex_color.upper().lstrip("#"))
    if filters.pieces:
        conditions.append(model.piece.in_(filters.pieces))
    if filters.min_price is not None:
        conditions.append(model.price >= filters.min_price)
    if filters.max_price is not None:
        conditions.append(model.price <= filters.max_price)
    if filters.bin_filter == "bin":
        conditions.append(model.is_bin.is_(True))
    elif filters.bin_filter == "nobin":
        conditions.append(model.is_bin.is_(False))

    if filters.tier:
        if tier_bounds(filters.tier) is None:
            return false()
        conditions.append(tier_col == filters.tier)

    if filters.set_name:
        target_hexes = get_set_target_hexes(filters.set_name)
        if not target_hexes:
            return false()
        conditions.append(target_col.in_(target_hexes))

    return and_(*conditions) if conditions else true()


def sort_column(model, sort_by: str):
    if sort_by == "price":
        return model.price
    if model is ActiveAuction:
        return model.end_time
    return model.timestamp


def serialize(auction, response_cls, include_dyes: bool):
    data = {
        field: getattr(auction, field)
        for field in response_cls.model_fields
        if hasattr(auction, field)
    }
    target_hex = auction.target_hex_all if include_dyes else auction.target_hex_keys
    delta_e = auction.delta_e_all if include_dyes else auction.delta_e_keys

    data["target_hex"] = target_hex or ""
    data["delta_e"] = round(delta_e, 2) if delta_e is not None else 0.0
    data["abs_diff"] = compute_abs_diff(auction.hex_color.upper(), target_hex)
    data["tier"] = get_tier(delta_e)
    data["set_name"] = get_name_for_hex(target_hex)
    return response_cls(**data)


async def list_auctions(
    db: AsyncSession,
    model,
    response_cls,
    filters: AuctionFilters,
    sort_by: str,
    sort_order: Literal["asc", "desc"],
    skip: int,
    limit: int,
) -> tuple[list, int]:
    where = build_where(model, filters)
    total = await db.scalar(select(func.count()).select_from(model).where(where)) or 0

    order_func = desc if sort_order == "desc" else asc
    result = await db.execute(
        select(model)
        .where(where)
        .order_by(order_func(sort_column(model, sort_by)), model.auction_id)
        .offset(skip)
        .limit(limit)
    )
    auctions = [serialize(a, response_cls, filters.include_dyes) for a in result.scalars().all()]
    return auctions, total
