import re
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from redis.asyncio import Redis

from app.dependencies import get_db, get_redis
from app.services.compute import (
    get_lab, delta_e, ALL_LAB, 
    KEY_ITEM_HEXES, HEX_TO_ITEMS, ITEM_TO_INFO, ITEM_TO_SET
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


def find_top_matches(hex_code: str, limit: int = 3) -> list[dict]:
    search_lab = get_lab(hex_code)
    
    seen_items = {}
    for target_hex, piece_idx in KEY_ITEM_HEXES:
        if target_hex in seen_items:
            continue
        target_lab = get_lab(target_hex)
        de = float(delta_e(search_lab, target_lab))
        seen_items[target_hex] = de
    
    sorted_matches = sorted(seen_items.items(), key=lambda x: x[1])[:limit]
    
    results = []
    for target_hex, de in sorted_matches:
        item_names = HEX_TO_ITEMS.get(target_hex, [])
        if not item_names:
            continue
        
        item_name = item_names[0]
        info = ITEM_TO_INFO.get(item_name, {})
        set_name = ITEM_TO_SET.get(item_name, item_name)
        pieces = [PIECE_NAMES[p] for p in info.get("pieces", [])]
        
        results.append({
            "item_name": set_name,
            "target_hex": target_hex,
            "delta_e": round(de, 2),
            "pieces": pieces,
        })
    
    return results


@router.get("/{hex_code}")
async def get_hex_info(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    hex_code = validate_hex(hex_code)
    top_matches = find_top_matches(hex_code, limit=3)
    
    result = await db.execute(
        select(User).options(selectinload(User.seymours)).where(User.seymours.any())
    )
    users = result.scalars().all()
    
    for match in top_matches:
        target_hex = match["target_hex"]
        target_lab = get_lab(target_hex)
        users_by_tier = {"t1": [], "t2": [], "t3": []}
        
        for user in users:
            best_de = None
            for s in user.seymours:
                seymour_lab = get_lab(s.hex_color)
                de = float(delta_e(seymour_lab, target_lab))
                if de < 10.0:
                    if best_de is None or de < best_de:
                        best_de = de
            
            if best_de is not None:
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
