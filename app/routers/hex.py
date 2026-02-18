import re
import asyncio
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from redis.asyncio import Redis

from app.dependencies import get_db, get_redis
from app.services.compute import (
    find_top_hex_matches, find_users_near_hex,
    HEX_TO_ITEMS, ITEM_TO_INFO, ITEM_TO_SET, HEX_TO_DYE_STAGE, FADE_DYES
)
from app.models import User, Seymour

router = APIRouter()

TIER_THRESHOLDS = {
    "t1": (0, 2),
    "t2": (2, 5),
    "t3": (5, 10),
}

PIECE_NAMES = ["helmet", "chestplate", "leggings", "boots"]

HEX_PATTERN = re.compile(r'^[0-9A-Fa-f]{6}$')


def validate_hex(hex_code: str) -> str:
    hex_code = hex_code.upper().lstrip('#')
    if not HEX_PATTERN.match(hex_code):
        raise HTTPException(status_code=400, detail="Invalid hex code format")
    return hex_code


def get_tier(de: float) -> str | None:
    for tier, (min_de, max_de) in TIER_THRESHOLDS.items():
        if min_de <= de < max_de:
            return tier
    return None


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
        dye_info = HEX_TO_DYE_STAGE[target_hex][0]
        dye_name, stage = dye_info
        return {
            "item_name": f"{dye_name} ({stage})",
            "target_hex": target_hex,
            "delta_e": round(de, 2),
            "pieces": PIECE_NAMES,
        }
    
    return None


@router.get("/{hex_code}")
async def get_hex_info(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    include_dyes: bool = False
):
    hex_code = validate_hex(hex_code)
    
    async def fetch_users():
        result = await db.execute(
            select(User).options(selectinload(User.seymours)).where(User.seymours.any())
        )
        return result.scalars().all()
    
    raw_matches, users = await asyncio.gather(
        asyncio.to_thread(find_top_hex_matches, hex_code, 3, include_dyes),
        fetch_users()
    )
    top_matches = [m for t, d in raw_matches if (m := build_match_info(t, d, include_dyes))]
    
    user_seymours = {
        user.id: (user, [(s.hex_color, s.item_name) for s in user.seymours])
        for user in users
    }
    
    for match in top_matches:
        target_hex = match["target_hex"]
        users_by_tier = {"t1": [], "t2": [], "t3": []}
        
        for user_id, (user, seymour_list) in user_seymours.items():
            nearby = find_users_near_hex(seymour_list, target_hex, threshold=10.0)
            if not nearby:
                continue
            best_de = min(de for _, _, de in nearby)
            tier = get_tier(best_de)
            if tier:
                users_by_tier[tier].append({
                    "username": user.username,
                    "discord_id": user.discord_id,
                    "avatar_hash": user.avatar_hash,
                    "delta_e": round(best_de, 2)
                })
        
        for tier in users_by_tier:
            users_by_tier[tier].sort(key=lambda x: x["delta_e"])
        
        match["users_by_tier"] = users_by_tier
    
    return {
        "hex_code": hex_code,
        "top_matches": top_matches
    }
