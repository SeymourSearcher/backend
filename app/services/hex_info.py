import asyncio
import re

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.repositories.seymour_repo import SeymourRepository
from app.services.cache import get_cached_hex_info, set_cached_hex_info
from app.services.compute import (
    HEX_NEIGHBORHOODS_ALL,
    HEX_NEIGHBORHOODS_KEYS,
    HEX_TO_DYE_STAGE,
    HEX_TO_ITEMS,
    ITEM_TO_INFO,
    ITEM_TO_SET,
    delta_e,
    find_top_hex_matches,
    get_lab,
)
from app.pieces import Piece
from app.services.matching import get_tier

HEX_PATTERN = re.compile(r'^[0-9A-Fa-f]{6}$')


def validate_hex(hex_code: str) -> str:
    hex_code = hex_code.upper().lstrip('#')
    if not HEX_PATTERN.match(hex_code):
        raise InvalidInputError("Invalid hex code format")
    return hex_code


def build_match_info(target_hex: str, de: float, include_dyes: bool = False) -> dict | None:
    item_names = HEX_TO_ITEMS.get(target_hex, [])
    if item_names:
        item_name = item_names[0]
        info = ITEM_TO_INFO.get(item_name, {})
        return {
            "target_name": ITEM_TO_SET.get(item_name, item_name),
            "target_hex": target_hex,
            "delta_e": round(de, 2),
            "pieces": info.get("pieces", []),
        }

    if include_dyes and target_hex in HEX_TO_DYE_STAGE:
        dye_name, stage = HEX_TO_DYE_STAGE[target_hex][0]
        return {
            "target_name": f"{dye_name} Stage {stage}",
            "target_hex": target_hex,
            "delta_e": round(de, 2),
            "pieces": list(Piece),
        }

    return None


def compute_user_tiers(
    owner_hexes: list[tuple[int, str, int, str | None, str]],
    targets: list[tuple[str, set[int]]],
) -> list[dict]:
    """Best tier per user for each target, in one pass over the table.

    Neighbourhoods run to ~10^5 hex codes, so membership cannot be pushed into
    SQL; the win is walking the rows once for all targets instead of once each.
    """
    target_labs = [get_lab(target_hex) for target_hex, _ in targets]
    best_per_target: list[dict] = [{} for _ in targets]

    for user_id, username, discord_id, avatar_hash, hex_color in owner_hexes:
        hex_int = int(hex_color, 16)
        lab = None

        for i, (_, neighborhood) in enumerate(targets):
            if hex_int not in neighborhood:
                continue
            if lab is None:
                lab = get_lab(hex_color)
            de_val = float(delta_e(lab, target_labs[i]))

            best = best_per_target[i]
            previous = best.get(user_id)
            if previous is None or de_val < previous[1]:
                best[user_id] = ((username, discord_id, avatar_hash), de_val)

    results = []
    for best in best_per_target:
        users_by_tier = {"t1": [], "t2": [], "t3": []}
        for (username, discord_id, avatar_hash), best_de in best.values():
            tier = get_tier(best_de)
            if tier:
                users_by_tier[tier].append({
                    "username": username,
                    "discord_id": str(discord_id),
                    "avatar_hash": avatar_hash,
                    "delta_e": round(best_de, 2),
                })
        for tier in users_by_tier:
            users_by_tier[tier].sort(key=lambda x: x["delta_e"])
        results.append(users_by_tier)

    return results


async def get_hex_info(
    db: AsyncSession, redis: Redis, hex_code: str, include_dyes: bool
) -> dict:
    hex_code = validate_hex(hex_code)

    cached = await get_cached_hex_info(redis, hex_code, include_dyes)
    if cached is not None:
        return cached

    raw_matches = await asyncio.to_thread(find_top_hex_matches, hex_code, 3, include_dyes)
    top_matches = [m for t, d in raw_matches if (m := build_match_info(t, d, include_dyes))]

    neighborhoods_data = HEX_NEIGHBORHOODS_ALL if include_dyes else HEX_NEIGHBORHOODS_KEYS
    targets = [
        (m["target_hex"], set(neighborhoods_data.get(m["target_hex"], [])))
        for m in top_matches
    ]

    owner_hexes = await SeymourRepository(db).list_owner_hexes()
    tiers = await asyncio.to_thread(compute_user_tiers, owner_hexes, targets)
    for match, users_by_tier in zip(top_matches, tiers):
        match["users_by_tier"] = users_by_tier

    info = {"hex_code": hex_code, "top_matches": top_matches}
    await set_cached_hex_info(redis, hex_code, include_dyes, info)
    return info
