import asyncio
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.models import Seymour, User
from app.repositories.seymour_repo import SeymourRepository
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
from app.services.matching import get_tier

PIECE_NAMES = ["helmet", "chestplate", "leggings", "boots"]

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
            "item_name": ITEM_TO_SET.get(item_name, item_name),
            "target_hex": target_hex,
            "delta_e": round(de, 2),
            "pieces": [PIECE_NAMES[p] for p in info.get("pieces", [])],
        }

    if include_dyes and target_hex in HEX_TO_DYE_STAGE:
        dye_name, stage = HEX_TO_DYE_STAGE[target_hex][0]
        return {
            "item_name": f"{dye_name} ({stage})",
            "target_hex": target_hex,
            "delta_e": round(de, 2),
            "pieces": PIECE_NAMES,
        }

    return None


def compute_user_tiers_for_target(
    user_seymours: list[tuple[User, Seymour]], target_hex: str, neighborhood: set[int]
) -> dict:
    target_lab = get_lab(target_hex)
    user_best = {}

    for user, seymour in user_seymours:
        if int(seymour.hex_color, 16) not in neighborhood:
            continue
        de_val = float(delta_e(get_lab(seymour.hex_color), target_lab))
        if user.id not in user_best or de_val < user_best[user.id][1]:
            user_best[user.id] = (user, de_val)

    users_by_tier = {"t1": [], "t2": [], "t3": []}
    for user, best_de in user_best.values():
        tier = get_tier(best_de)
        if tier:
            users_by_tier[tier].append({
                "username": user.username,
                "discord_id": str(user.discord_id),
                "avatar_hash": user.avatar_hash,
                "delta_e": round(best_de, 2)
            })

    for tier in users_by_tier:
        users_by_tier[tier].sort(key=lambda x: x["delta_e"])
    return users_by_tier


async def get_hex_info(db: AsyncSession, hex_code: str, include_dyes: bool) -> dict:
    hex_code = validate_hex(hex_code)

    raw_matches = await asyncio.to_thread(find_top_hex_matches, hex_code, 3, include_dyes)
    top_matches = [m for t, d in raw_matches if (m := build_match_info(t, d, include_dyes))]

    neighborhoods_data = HEX_NEIGHBORHOODS_ALL if include_dyes else HEX_NEIGHBORHOODS_KEYS
    user_seymours = await SeymourRepository(db).list_with_users()

    for match in top_matches:
        target_hex = match["target_hex"]
        match["users_by_tier"] = compute_user_tiers_for_target(
            user_seymours, target_hex, set(neighborhoods_data.get(target_hex, []))
        )

    return {"hex_code": hex_code, "top_matches": top_matches}
