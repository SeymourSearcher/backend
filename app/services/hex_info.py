import asyncio
import re

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.services.cache import HEX_INFO
from app.services.owner_hexes import OwnerHexes, get_owner_hexes
from app.services.compute import (
    HEX_NEIGHBORHOODS_ALL,
    HEX_NEIGHBORHOODS_KEYS,
    ITEM_TO_INFO,
    delta_e,
    find_top_hex_matches,
    get_lab,
)
from app.pieces import Piece
from app.services.matching import (
    dye_label_for_hex,
    get_tier,
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


def compute_user_tiers(
    owners: OwnerHexes,
    targets: list[tuple[str, set[int]]],
) -> list[dict]:
    """Best tier per user for each target, in one pass over the table.

    Neighbourhoods run to ~10^5 hex codes, so membership cannot be pushed into
    SQL; the win is walking the rows once for all targets instead of once each.
    """
    target_labs = [get_lab(target_hex) for target_hex, _ in targets]
    best_per_target: list[dict] = [{} for _ in targets]

    for user_index, hex_int in zip(owners.idx, owners.hexes):
        lab = None

        for i, (_, neighborhood) in enumerate(targets):
            if hex_int not in neighborhood:
                continue
            if lab is None:
                lab = get_lab(f"{hex_int:06X}")
            de_val = float(delta_e(lab, target_labs[i]))

            best = best_per_target[i]
            previous = best.get(user_index)
            if previous is None or de_val < previous:
                best[user_index] = de_val

    results = []
    for best in best_per_target:
        users_by_tier = {"t1": [], "t2": [], "t3": []}
        for user_index, best_de in best.items():
            _, username, discord_id, avatar_hash = owners.users[user_index]
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

    cached = await HEX_INFO.get(redis, hex_code, include_dyes)
    if cached is not None:
        return cached

    raw_matches = await asyncio.to_thread(find_top_hex_matches, hex_code, 3, include_dyes)
    top_matches = [m for t, d in raw_matches if (m := build_match_info(t, d, include_dyes))]

    neighborhoods_data = HEX_NEIGHBORHOODS_ALL if include_dyes else HEX_NEIGHBORHOODS_KEYS
    targets = [
        (m["target_hex"], set(neighborhoods_data.get(m["target_hex"], [])))
        for m in top_matches
    ]

    owners = await get_owner_hexes(db, redis)
    tiers = await asyncio.to_thread(compute_user_tiers, owners, targets)
    for match, users_by_tier in zip(top_matches, tiers):
        match["users_by_tier"] = users_by_tier

    info = {"hex_code": hex_code, "top_matches": top_matches}
    await HEX_INFO.set(redis, info, hex_code, include_dyes)
    return info
