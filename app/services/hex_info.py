import asyncio
import re

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.models import EndedAuction
from app.schemas.auction import EndedAuctionResponse
from app.services.auction_queries import AuctionFilters, attach_names, list_auctions
from app.services.auction_stats import compute_auction_stats
from app.services.cache import HEX_INFO, OWNER_MATCHES
from app.services.owner_hexes import get_owner_hexes
from app.services.owner_matches import (
    MAX_DELTA_E,
    find_owner_matches,
    tier_users,
)
from app.services.compute import ITEM_TO_INFO, delta_e, find_top_hex_matches, get_lab
from app.services.hex_prices import price_estimates
from app.pieces import Piece
from app.services.matching import (
    compute_abs_diff,
    dye_label_for_hex,
    get_tier,
    key_item_for_hex,
    set_name_for_hex,
)

CATALOG_MATCHES = 3

HEX_PATTERN = re.compile(r'^[0-9A-Fa-f]{6}$')


def validate_hex(hex_code: str) -> str:
    hex_code = hex_code.upper().lstrip('#')
    if not HEX_PATTERN.match(hex_code):
        raise InvalidInputError("Invalid hex code format")
    return hex_code


def build_match_info(
    search_hex: str, target_hex: str, de: float, include_dyes: bool = False
) -> dict | None:
    # Two distances, deliberately: ΔE is perceptual and drives the tiers, ABS is
    # the raw RGB gap people read off the hex codes themselves.
    common = {
        "target_hex": target_hex,
        "delta_e": round(de, 2),
        "abs_diff": compute_abs_diff(search_hex, target_hex),
    }

    item_name = key_item_for_hex(target_hex)
    if item_name is not None:
        return {
            **common,
            "target_name": set_name_for_hex(target_hex),
            "pieces": ITEM_TO_INFO.get(item_name, {}).get("pieces", []),
        }

    dye_label = dye_label_for_hex(target_hex, with_stage=True) if include_dyes else None
    if dye_label is not None:
        return {
            **common,
            "target_name": dye_label,
            # A dye colour is available on every piece.
            "pieces": list(Piece),
        }

    return None


def _tiers_per_match(owners, top_matches: list[dict]) -> list[dict]:
    return [tier_users(owners, m["target_hex"]) for m in top_matches]


def _pieces_key(pieces: list[int] | None) -> str:
    return ",".join(map(str, pieces)) if pieces else "all"


async def get_hex_info(
    db: AsyncSession, redis: Redis, hex_code: str, include_dyes: bool, pieces: list[int] | None
) -> dict:
    hex_code = validate_hex(hex_code)

    cached = await HEX_INFO.get(redis, hex_code, include_dyes, _pieces_key(pieces))
    if cached is not None:
        return cached

    # One extra, so dropping the searched colour itself still leaves three
    # genuinely *nearby* items rather than two.
    raw_matches = await asyncio.to_thread(
        find_top_hex_matches, hex_code, CATALOG_MATCHES + 1, include_dyes, pieces
    )
    matches = [
        m for t, d in raw_matches if (m := build_match_info(hex_code, t, d, include_dyes))
    ]

    exact_match = next((m for m in matches if m["target_hex"] == hex_code), None)
    top_matches = [m for m in matches if m is not exact_match][:CATALOG_MATCHES]

    # The exact match is enriched alongside the rest so the hero can price the
    # colour you actually searched rather than the next item along.
    enriched = ([exact_match] if exact_match else []) + top_matches

    owners = await get_owner_hexes(db, redis)
    tiers = await asyncio.to_thread(_tiers_per_match, owners, enriched)

    # Priced at the tier the searched colour earns against *that* item, so a
    # near-miss is never quoted a perfect match's price.
    prices = await price_estimates(
        db,
        redis,
        [(m["target_hex"], get_tier(m["delta_e"])) for m in enriched],
        include_dyes,
        pieces,
    )

    for match, users_by_tier, price in zip(enriched, tiers, prices):
        match["users_by_tier"] = users_by_tier
        match["price"] = price

    info = {"hex_code": hex_code, "exact_match": exact_match, "top_matches": top_matches}
    await HEX_INFO.set(redis, info, hex_code, include_dyes, _pieces_key(pieces))
    return info


def _empty_stats() -> dict:
    """Built per call: a module-level constant would share its inner lists."""
    return {
        "total": 0,
        "avg_price": 0.0,
        "median_price": 0.0,
        "total_volume": 0,
        "bin_count": 0,
        "auction_count": 0,
        "t1_count": 0,
        "t2_count": 0,
        "t3_count": 0,
        "price_distribution": [],
        "hourly_activity": [0] * 24,
    }


ALL_TIERS = "all"


async def _resolve_reference(
    db: AsyncSession,
    redis: Redis,
    hex_code: str,
    include_dyes: bool,
    pieces: list[int] | None,
    tier: str | None,
) -> tuple[dict, AuctionFilters | None]:
    """The colour a search prices, and the filter isolating that colour's sales.

    The reference is the one the hero prices -- the exact catalog colour when
    there is one, otherwise the nearest -- so everything built on this describes
    the headline figure rather than quoting a different one.

    tier overrides which band is used: a tier key to inspect another one, or
    ALL_TIERS for the item's whole range. Left unset it follows the searched
    colour, which is what makes the default agree with the hero.

    The filter is None when there is nothing to select. Past the last tier there
    is no band to default to, and quietly dropping the predicate would widen the
    result to every sale of the item; choosing a tier explicitly is still
    honoured.
    """
    info = await get_hex_info(db, redis, hex_code, include_dyes, pieces)
    reference = info["exact_match"] or next(iter(info["top_matches"]), None)
    search_tier = get_tier(reference["delta_e"]) if reference else None

    applied = None if tier == ALL_TIERS else (tier or search_tier)
    context = {
        "target_name": reference["target_name"] if reference else None,
        "target_hex": reference["target_hex"] if reference else None,
        "search_tier": search_tier,
        "tier": applied,
    }

    if reference is None or (tier is None and applied is None):
        return context, None

    return context, AuctionFilters(
        target_hex=reference["target_hex"],
        tier=applied,
        pieces=pieces,
        include_dyes=include_dyes,
    )


async def get_hex_auction_stats(
    db: AsyncSession,
    redis: Redis,
    hex_code: str,
    include_dyes: bool,
    pieces: list[int] | None,
    tier: str | None = None,
) -> dict:
    """Sale statistics for the colour a search resolves to. See _resolve_reference."""
    context, filters = await _resolve_reference(
        db, redis, hex_code, include_dyes, pieces, tier
    )
    if filters is None:
        return {**context, **_empty_stats()}

    return {**context, **await compute_auction_stats(db, filters, redis=redis)}


async def get_hex_sales(
    db: AsyncSession,
    redis: Redis,
    hex_code: str,
    include_dyes: bool,
    pieces: list[int] | None,
    tier: str | None,
    limit: int,
    offset: int,
) -> dict:
    """The individual sales behind get_hex_auction_stats, newest first.

    Same reference and same band, so these rows are the ones the median above
    them was computed from. Each carries search_delta_e -- how far that
    auction's actual colour sat from the *searched* hex, which the row's own
    delta_e (measured to its catalog target) does not answer.
    """
    hex_code = validate_hex(hex_code)
    context, filters = await _resolve_reference(
        db, redis, hex_code, include_dyes, pieces, tier
    )
    if filters is None:
        return {**context, "total": 0, "items": []}

    auctions, total = await list_auctions(
        db, EndedAuction, EndedAuctionResponse, filters,
        "timestamp", "desc", offset, limit, redis=redis,
    )
    await attach_names(db, auctions, "seller_uuid", "buyer_uuid")

    search_lab = get_lab(hex_code)
    items = [
        {
            **auction.model_dump(),
            "search_delta_e": round(
                float(delta_e(search_lab, get_lab(auction.hex_color.upper()))), 2
            ),
        }
        for auction in auctions
    ]
    return {**context, "total": total, "items": items}


async def get_hex_owners(
    db: AsyncSession,
    redis: Redis,
    hex_code: str,
    limit: int,
    offset: int,
    pieces: list[int] | None,
) -> dict:
    """One page of the holdings within MAX_DELTA_E of hex_code.

    include_dyes deliberately plays no part: the catalog decides which *items*
    are near a colour, but ownership is measured against the colour itself.
    """
    hex_code = validate_hex(hex_code)

    matches = await OWNER_MATCHES.get(redis, hex_code)
    if matches is None:
        owners = await get_owner_hexes(db, redis)
        matches = await asyncio.to_thread(find_owner_matches, owners, hex_code)
        await OWNER_MATCHES.set(redis, matches, hex_code)

    # Filtered after the cache read, not before: every slot selection shares one
    # cached scan of the table.
    if pieces:
        matches = [m for m in matches if m["piece"] in pieces]

    return {
        "total": len(matches),
        "max_delta_e": MAX_DELTA_E,
        "items": matches[offset:offset + limit],
    }
