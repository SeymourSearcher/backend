import asyncio
import re

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.services.auction_queries import AuctionFilters
from app.services.auction_stats import compute_auction_stats
from app.services.cache import HEX_INFO, OWNER_MATCHES
from app.services.owner_hexes import get_owner_hexes
from app.services.owner_matches import (
    MAX_DELTA_E,
    find_owner_matches,
    tier_users,
)
from app.services.compute import ITEM_TO_INFO, find_top_hex_matches
from app.services.hex_prices import price_estimates
from app.pieces import Piece
from app.services.matching import (
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


def build_match_info(target_hex: str, de: float, include_dyes: bool = False) -> dict | None:
    item_name = key_item_for_hex(target_hex)
    if item_name is not None:
        return {
            "target_name": set_name_for_hex(target_hex),
            "target_hex": target_hex,
            "delta_e": round(de, 2),
            "pieces": ITEM_TO_INFO.get(item_name, {}).get("pieces", []),
        }

    dye_label = dye_label_for_hex(target_hex, with_stage=True) if include_dyes else None
    if dye_label is not None:
        return {
            "target_name": dye_label,
            "target_hex": target_hex,
            "delta_e": round(de, 2),
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
    matches = [m for t, d in raw_matches if (m := build_match_info(t, d, include_dyes))]

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


async def get_hex_auction_stats(
    db: AsyncSession,
    redis: Redis,
    hex_code: str,
    include_dyes: bool,
    pieces: list[int] | None,
    tier: str | None = None,
) -> dict:
    """Sale statistics for the colour a search resolves to.

    Same reference the hero prices -- the exact catalog colour when there is
    one, otherwise the nearest -- so the section elaborates on the headline
    figure rather than quoting a different one.

    tier overrides which band is priced: a tier key to inspect another one, or
    ALL_TIERS for the item's whole range. Left unset it follows the searched
    colour, which is what makes the default agree with the hero.
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

    # Past the last tier there is no band to default to, and quietly dropping
    # the filter would quote every sale of the item instead. Choosing a tier
    # explicitly is still honoured.
    if reference is None or (tier is None and applied is None):
        return {**context, **_empty_stats()}

    filters = AuctionFilters(
        target_hex=reference["target_hex"],
        tier=applied,
        pieces=pieces,
        include_dyes=include_dyes,
    )
    return {**context, **await compute_auction_stats(db, filters, redis=redis)}


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
