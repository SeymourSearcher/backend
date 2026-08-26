"""Query construction and serialisation for the auction tables.

Every filter here resolves to a SQL predicate over persisted columns, so
filtering, sorting and pagination all happen in the database. See
app/services/matching.py for why tier is a range predicate rather than a column.
"""
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy import and_, asc, desc, false, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError
from app.models import ActiveAuction
from app.repositories.auction_repo import AuctionRepository
from app.services.cache import AUCTION_COUNT
from app.services.matching import (
    compute_abs_diff,
    get_name_for_hex,
    get_set_target_hexes,
    get_tier,
    tier_bounds,
)
from app.services.player import lookup_names


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


def order_by_clause(model, sort_by: str, sort_order: str) -> list:
    """Sort column then auction_id, both in the same direction.

    The tiebreaker only exists to make pagination deterministic, so its
    direction is free -- but it must follow sort_order. Pinning it ASC while
    the sort column flips DESC produces a mixed-direction sort that matches no
    index, and MySQL falls back to a full scan plus filesort: 310ms against 26ms
    on 171k ended auctions.
    """
    order_func = desc if sort_order == "desc" else asc
    return [order_func(sort_column(model, sort_by)), order_func(model.auction_id)]


def match_fields(auction, include_dyes: bool) -> dict:
    """Derived match columns shared by the auction and recent-bid payloads."""
    target_hex = (auction.target_hex_all if include_dyes else auction.target_hex_keys) or ""
    delta_e = auction.delta_e_all if include_dyes else auction.delta_e_keys
    return {
        "target_hex": target_hex,
        "delta_e": round(delta_e, 2) if delta_e is not None else 0.0,
        "abs_diff": compute_abs_diff(auction.hex_color.upper(), target_hex),
        "set_name": get_name_for_hex(target_hex),
    }


def serialize(auction, response_cls, include_dyes: bool):
    data = {
        field: getattr(auction, field)
        for field in response_cls.model_fields
        if hasattr(auction, field)
    }
    data.update(match_fields(auction, include_dyes))
    data["tier"] = get_tier(auction.delta_e_all if include_dyes else auction.delta_e_keys)
    return response_cls(**data)


def _count_key(model, filters: AuctionFilters) -> str:
    """Everything that changes the count, and nothing that doesn't.

    Sort and paging are excluded deliberately -- they reorder or window the
    result but never resize it, and folding them in would shard the cache into
    one entry per column-and-direction for no gain.
    """
    payload = json.dumps(asdict(filters), sort_keys=True, default=str)
    return f"{model.__tablename__}:{hashlib.md5(payload.encode()).hexdigest()}"


async def count_auctions(db: AsyncSession, redis: Redis | None, model, filters, where) -> int:
    """Matching rows, cached. Only a display label reads this.

    A miss costs a full index scan of every matching row, so a stale count is
    worth more than a live one here: it is never older than the sync that
    produces the rows it describes.
    """
    if redis is None:
        return await db.scalar(select(func.count()).select_from(model).where(where)) or 0

    key = _count_key(model, filters)
    cached = await AUCTION_COUNT.get(redis, key)
    if cached is not None:
        return cached

    total = await db.scalar(select(func.count()).select_from(model).where(where)) or 0
    await AUCTION_COUNT.set(redis, total, key)
    return total


async def list_auctions(
    db: AsyncSession,
    model,
    response_cls,
    filters: AuctionFilters,
    sort_by: str,
    sort_order: Literal["asc", "desc"],
    skip: int,
    limit: int,
    redis: Redis | None = None,
) -> tuple[list, int]:
    where = build_where(model, filters)
    total = await count_auctions(db, redis, model, filters, where)

    order = order_by_clause(model, sort_by, sort_order)

    # Deferred join: page over auction_id alone, then fetch full rows for the
    # page you keep. OFFSET n on SELECT * costs n random primary-key lookups
    # before discarding them, and past ~10k rows MySQL gives up on the ordering
    # index entirely and full-scans. Paging an index-only subquery avoids both:
    # 543ms -> 30ms at offset 10k, 658ms -> 66ms at 100k, and no loss at offset 0.
    page = (
        select(model.auction_id)
        .where(where)
        .order_by(*order)
        .offset(skip)
        .limit(limit)
        .subquery()
    )
    result = await db.execute(
        # The join does not preserve the subquery's order, so re-apply it.
        select(model).join(page, model.auction_id == page.c.auction_id).order_by(*order)
    )
    auctions = [serialize(a, response_cls, filters.include_dyes) for a in result.scalars().all()]
    return auctions, total


async def attach_names(db: AsyncSession, auctions: list, *fields: str) -> None:
    uuids = {getattr(a, f) for a in auctions for f in fields if getattr(a, f)}
    if not uuids:
        return
    names = await lookup_names(db, list(uuids))
    for auction in auctions:
        for field in fields:
            setattr(auction, f"{field.removesuffix('_uuid')}_name", names.get(getattr(auction, field)))


async def get_auction(db: AsyncSession, model, auction_id: str, response_cls, include_dyes: bool):
    repo = AuctionRepository(db)
    auction = (
        await repo.get_active(auction_id)
        if model is ActiveAuction
        else await repo.get_ended(auction_id)
    )
    if not auction:
        raise NotFoundError("Auction not found")
    return serialize(auction, response_cls, include_dyes)


async def recent_bids(db: AsyncSession, limit: int, include_dyes: bool) -> list[dict]:
    rows = await AuctionRepository(db).recent_bids(limit)
    names = await lookup_names(db, list({bid.bidder_uuid for bid, _ in rows}))

    return [
        {
            "auction_id": auction.auction_id,
            "bidder_uuid": bid.bidder_uuid,
            "bidder_name": names.get(bid.bidder_uuid),
            "amount": bid.amount,
            "timestamp": bid.timestamp,
            "hex_color": auction.hex_color,
            "piece": auction.piece,
            **match_fields(auction, include_dyes),
        }
        for bid, auction in rows
    ]
