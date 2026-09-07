import asyncio
import re

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.services.cache import HEX_INFO, OWNER_MATCHES
from app.services.owner_hexes import get_owner_hexes
from app.services.owner_matches import (
    MAX_DELTA_E,
    find_owner_matches,
    tier_users,
)
from app.services.compute import ITEM_TO_INFO, find_top_hex_matches
from app.pieces import Piece
from app.services.matching import (
    dye_label_for_hex,
    key_item_for_hex,
    set_name_for_hex,
)

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


async def get_hex_info(
    db: AsyncSession, redis: Redis, hex_code: str, include_dyes: bool
) -> dict:
    hex_code = validate_hex(hex_code)

    cached = await HEX_INFO.get(redis, hex_code, include_dyes)
    if cached is not None:
        return cached

    raw_matches = await asyncio.to_thread(find_top_hex_matches, hex_code, 3, include_dyes)
    top_matches = [m for t, d in raw_matches if (m := build_match_info(t, d, include_dyes))]

    owners = await get_owner_hexes(db, redis)
    tiers = await asyncio.to_thread(_tiers_per_match, owners, top_matches)
    for match, users_by_tier in zip(top_matches, tiers):
        match["users_by_tier"] = users_by_tier

    info = {"hex_code": hex_code, "top_matches": top_matches}
    await HEX_INFO.set(redis, info, hex_code, include_dyes)
    return info


async def get_hex_owners(
    db: AsyncSession, redis: Redis, hex_code: str, limit: int, offset: int
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

    return {
        "total": len(matches),
        "max_delta_e": MAX_DELTA_E,
        "items": matches[offset:offset + limit],
    }
