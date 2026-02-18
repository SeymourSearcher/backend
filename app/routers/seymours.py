from collections import Counter
from datetime import datetime
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, insert
from redis.asyncio import Redis

from app.dependencies import get_db, get_current_user, get_redis, get_optional_user
from app.schemas import SeymourResponse, UploadResponse, SeymourWithMatchResponse, MatchInfo
from app.schemas.seymour import ITEM_DISPLAY_NAMES
from app.services import parse_seymour_file
from app.services.stats import is_star, _contains_word
from app.services.cache import (
    invalidate_global_stats,
    invalidate_user_seymours,
    invalidate_rankings,
    get_cached_seymour_results,
    set_cached_seymour_results,
    get_cached_available_filters,
    set_cached_available_filters,
    get_cached_user_seymours,
    set_cached_user_seymours,
    get_cached_custom_sets,
    set_cached_custom_sets,
)
from app.services.compute import find_matching_seymours, find_sets, find_incomplete_armor_sets, find_incomplete_dye_sets, find_custom_sets, KEY_ITEM_HEXES, HEX_TO_ITEMS, FADE_DYES, SETS, ITEM_TO_INFO, ITEM_TO_SET
from app.models import User, Seymour

router = APIRouter()


async def get_user_seymours(db: AsyncSession, redis: Redis, user_id: int) -> list[Seymour]:
    cached = await get_cached_user_seymours(redis, user_id)
    if cached is not None:
        return [Seymour(**s) for s in cached]
    
    result = await db.execute(select(Seymour).where(Seymour.user_id == user_id))
    seymours = list(result.scalars().all())
    await set_cached_user_seymours(redis, user_id, [
        {"id": s.id, "item_name": s.item_name, "hex_color": s.hex_color, "user_id": s.user_id}
        for s in seymours
    ])
    return seymours

TIER_THRESHOLDS = {
    "t1": (0, 2),
    "t2": (2, 5),
    "t3": (5, 10),
}


def _build_result(s: Seymour, match: Optional[dict] = None) -> dict:
    return {
        "id": s.id,
        "item_name": ITEM_DISPLAY_NAMES[s.item_name],
        "hex_color": s.hex_color,
        "match": match
    }


def _is_in_tier(de: float, tier: str) -> bool:
    min_de, max_de = TIER_THRESHOLDS[tier]
    return min_de <= de < max_de


def _get_matches(seymour_list: list[tuple], tier: Optional[str] = None, sub_filter: Optional[str] = None, include_dyes: bool = False) -> dict:
    from app.services.compute import HEX_TO_DYE_STAGE
    
    sub_filter_set = set(sub_filter.split(",")) if sub_filter else None
    matches = find_matching_seymours(seymour_list, KEY_ITEM_HEXES, threshold=10.0, best_only=True, include_dyes=include_dyes)
    match_info = {}
    for match in matches:
        user_hex, piece_idx = match[0]
        target_hex = match[1][0]
        de = match[2]
        
        if tier and not _is_in_tier(de, tier):
            continue
        
        item_names = HEX_TO_ITEMS.get(target_hex, [])
        if item_names:
            item_name = item_names[0]
            set_name = ITEM_TO_SET.get(item_name, item_name)
        elif include_dyes and target_hex in HEX_TO_DYE_STAGE:
            dye_info = HEX_TO_DYE_STAGE[target_hex][0]
            set_name = f"{dye_info[0]} Stage {dye_info[1]}"
        else:
            set_name = f"#{target_hex}"
        
        if sub_filter_set and set_name not in sub_filter_set:
            continue
        
        match_info[(user_hex, piece_idx)] = {
            "target_name": set_name,
            "target_hex": target_hex,
            "delta_e": round(de, 2)
        }
    return match_info


def _build_seymour_lookup(seymours: list[Seymour]) -> dict:
    lookup = {}
    for s in seymours:
        key = (s.hex_color, s.item_name)
        if key not in lookup:
            lookup[key] = []
        lookup[key].append(s)
    return lookup


def _filter_seymours_with_matches(seymours: list[Seymour], filter_type: str, sub_filter: Optional[str] = None, incomplete_only: bool = False, include_dyes: bool = False, custom_sets_cache: Optional[list] = None) -> list[dict]:
    seymour_list = [(s.hex_color, s.item_name) for s in seymours]
    
    if filter_type == "stars":
        return [_build_result(s) for s in seymours if is_star(s.hex_color)]
    
    if filter_type == "words":
        return [_build_result(s) for s in seymours if _contains_word(s.hex_color.upper())]
    
    if filter_type == "dupes":
        counts = Counter(seymour_list)
        return [_build_result(s) for s in seymours if counts[(s.hex_color, s.item_name)] > 1]
    
    if filter_type in TIER_THRESHOLDS:
        match_info = _get_matches(seymour_list, tier=filter_type, sub_filter=sub_filter, include_dyes=include_dyes)
        return [
            _build_result(s, match_info[key])
            for s in seymours
            if (key := (s.hex_color, s.item_name)) in match_info
        ]
    
    if filter_type == "sets":
        lookup = _build_seymour_lookup(seymours)
        results = []
        sub_filter_set = set(sub_filter.split(",")) if sub_filter else None
        
        if incomplete_only:
            incomplete_sets = find_incomplete_armor_sets(seymour_list)
            piece_to_item = ["Velvet Top Hat", "Cashmere Jacket", "Satin Trousers", "Oxford Shoes"]
            for set_name, owned_pieces, missing_pieces in incomplete_sets:
                if sub_filter_set and set_name not in sub_filter_set:
                    continue
                missing_names = [piece_to_item[p[1]] for p in missing_pieces if p[1] >= 0]
                for item_name, piece_idx, user_hex, de in owned_pieces:
                    key = (user_hex, piece_idx)
                    if key in lookup:
                        match = {
                            "target_name": set_name,
                            "target_hex": ITEM_TO_INFO.get(item_name, {}).get("hex", user_hex),
                            "delta_e": round(de, 2),
                            "missing": missing_names
                        }
                        results.append(_build_result(lookup[key][0], match))
        else:
            armor_sets, _ = find_sets(seymour_list)
            for set_name, pieces in armor_sets:
                if sub_filter_set and set_name not in sub_filter_set:
                    continue
                for item_name, piece_idx, user_hex, de in pieces:
                    key = (user_hex, piece_idx)
                    if key in lookup:
                        match = {
                            "target_name": set_name,
                            "target_hex": ITEM_TO_INFO.get(item_name, {}).get("hex", user_hex),
                            "delta_e": round(de, 2)
                        }
                        results.append(_build_result(lookup[key][0], match))
        return results
    
    if filter_type == "dyes":
        lookup = _build_seymour_lookup(seymours)
        results = []
        sub_filter_set = set(sub_filter.split(",")) if sub_filter else None
        
        if incomplete_only:
            incomplete_dyes = find_incomplete_dye_sets(seymour_list)
            piece_to_item = ["Velvet Top Hat", "Cashmere Jacket", "Satin Trousers", "Oxford Shoes"]
            for dye_name, stage, owned_pieces, missing_pieces in incomplete_dyes:
                if sub_filter_set and dye_name not in sub_filter_set:
                    continue
                target_hex = FADE_DYES.get(dye_name, {}).get(stage, "")
                missing_names = [piece_to_item[p[0]] for p in missing_pieces if p[0] >= 0]
                for piece_idx, user_hex, de in owned_pieces:
                    key = (user_hex, piece_idx)
                    if key in lookup:
                        match = {
                            "target_name": f"{dye_name} Stage {stage}",
                            "target_hex": target_hex,
                            "delta_e": round(de, 2),
                            "missing": missing_names
                        }
                        results.append(_build_result(lookup[key][0], match))
        else:
            _, dye_sets = find_sets(seymour_list)
            for dye_name, stage, pieces in dye_sets:
                if sub_filter_set and dye_name not in sub_filter_set:
                    continue
                target_hex = FADE_DYES.get(dye_name, {}).get(stage, "")
                for piece_idx, user_hex, de in pieces:
                    key = (user_hex, piece_idx)
                    if key in lookup:
                        match = {
                            "target_name": f"{dye_name} Stage {stage}",
                            "target_hex": target_hex,
                            "delta_e": round(de, 2)
                        }
                        results.append(_build_result(lookup[key][0], match))
        return results
    
    if filter_type == "all" and sub_filter:
        match_info = _get_matches(seymour_list, tier=None, sub_filter=sub_filter, include_dyes=include_dyes)
        return [
            _build_result(s, match_info[key])
            for s in seymours
            if (key := (s.hex_color, s.item_name)) in match_info
        ]
    
    if filter_type == "custom":
        lookup = _build_seymour_lookup(seymours)
        results = []
        custom_sets = custom_sets_cache if custom_sets_cache is not None else []
        
        for set_name, avg_de, pieces in custom_sets:
            for piece_idx, hex_color, piece_de in pieces:
                key = (hex_color, piece_idx)
                if key in lookup:
                    match = {
                        "target_name": set_name,
                        "target_hex": hex_color,
                        "delta_e": round(piece_de, 2)
                    }
                    results.append(_build_result(lookup[key][0], match))
        return results
    
    return [_build_result(s) for s in seymours]


def _get_available_tier_items(seymour_list: list[tuple], tier: str, include_dyes: bool = False) -> list[str]:
    match_info = _get_matches(seymour_list, tier=tier, include_dyes=include_dyes)
    return sorted({m["target_name"] for m in match_info.values()})


@router.get("/available-filters")
async def get_available_filters(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    filter: str = Query(...),
    incomplete_only: bool = Query(False),
    include_dyes: bool = Query(False)
):
    if filter in ("sets", "all"):
        return sorted(SETS.keys())
    if filter == "dyes":
        return sorted(FADE_DYES.keys())
    
    user = await get_optional_user(request, db)
    if not user:
        return []
    
    cached = await get_cached_available_filters(redis, user.id, filter, incomplete_only, include_dyes)
    if cached is not None:
        return cached
    
    seymours = await get_user_seymours(db, redis, user.id)
    seymour_list = [(s.hex_color, s.item_name) for s in seymours]
    
    filters = _compute_available_filters(seymour_list, filter, incomplete_only, include_dyes)
    await set_cached_available_filters(redis, user.id, filter, incomplete_only, include_dyes, filters)
    return filters


def _compute_available_filters(seymour_list: list[tuple], filter_type: str, incomplete_only: bool, include_dyes: bool = False) -> list[str]:
    if filter_type in TIER_THRESHOLDS:
        return _get_available_tier_items(seymour_list, filter_type, include_dyes)
    
    if filter_type in ("sets", "all"):
        return sorted(SETS.keys())
    
    if filter_type == "dyes":
        return sorted(FADE_DYES.keys())
    
    return []


def _sort_results(results: list[dict], sort: str, filter_type: str) -> list[dict]:
    if sort not in ("asc", "desc"):
        return results
    
    reverse = sort == "desc"
    
    if filter_type in ("sets", "dyes", "custom"):
        group_avgs = {}
        for r in results:
            name = r.get("match", {}).get("target_name", "")
            de = r.get("match", {}).get("delta_e")
            if name not in group_avgs:
                group_avgs[name] = []
            if de is not None:
                group_avgs[name].append(de)
        
        for name in group_avgs:
            vals = group_avgs[name]
            group_avgs[name] = sum(vals) / len(vals) if vals else float("inf")
        
        return sorted(results, key=lambda r: group_avgs.get(r.get("match", {}).get("target_name", ""), float("inf")), reverse=reverse)
    
    return sorted(results, key=lambda r: r.get("match", {}).get("delta_e") if r.get("match") else float("inf"), reverse=reverse)


@router.get("/", response_model=list[SeymourWithMatchResponse])
async def list_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    filter: Optional[str] = Query(None),
    sub_filter: Optional[str] = Query(None),
    incomplete_only: bool = Query(False),
    include_dyes: bool = Query(False),
    sort: Optional[str] = Query(None)
):
    filter_type = filter if filter and filter != "all" else "all"
    
    cached = await get_cached_seymour_results(redis, user.id, filter_type, sub_filter, incomplete_only, include_dyes)
    if cached is not None:
        sorted_results = _sort_results(cached, sort, filter_type) if sort else cached
        return sorted_results[skip:skip + limit]
    
    seymours = await get_user_seymours(db, redis, user.id)
    
    custom_sets_data = None
    if filter_type == "custom":
        seymour_list = [(s.hex_color, s.item_name) for s in seymours]
        custom_sets_data = await get_cached_custom_sets(redis, seymour_list)
        if custom_sets_data is None:
            custom_sets_data = find_custom_sets(seymour_list, max_avg_delta_e=1.0)
            await set_cached_custom_sets(redis, seymour_list, custom_sets_data)
    
    filtered = _filter_seymours_with_matches(seymours, filter_type, sub_filter, incomplete_only, include_dyes, custom_sets_data)
    
    await set_cached_seymour_results(redis, user.id, filter_type, sub_filter, incomplete_only, include_dyes, filtered)
    
    sorted_results = _sort_results(filtered, sort, filter_type) if sort else filtered
    return sorted_results[skip:skip + limit]


@router.post("/upload", response_model=UploadResponse)
async def upload_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    file: UploadFile = File(...)
):
    content = await file.read()
    text = content.decode("utf-8")
    
    parsed, skipped = parse_seymour_file(text)

    if len(parsed) > 100000:
        raise HTTPException(status_code=400, detail="Too many seymours to upload")
    
    await db.execute(delete(Seymour).where(Seymour.user_id == user.id))
    
    if parsed:
        values = [
            {"item_name": item.item_name, "hex_color": item.hex_color, "user_id": user.id}
            for item in parsed
        ]
        await db.execute(insert(Seymour), values)
    
    user.last_uploaded = datetime.utcnow()
    await db.commit()
    await invalidate_global_stats(redis)
    await invalidate_user_seymours(redis, user.id)
    await invalidate_rankings(redis)
    
    return UploadResponse(imported=len(parsed), skipped=skipped)


@router.get("/{seymour_id}", response_model=SeymourResponse)
async def get_seymour(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Seymour).where(Seymour.id == seymour_id, Seymour.user_id == user.id)
    )
    seymour = result.scalar_one_or_none()
    if not seymour:
        raise HTTPException(status_code=404, detail="Seymour not found")
    return seymour


@router.delete("/{seymour_id}")
async def delete_seymour(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    result = await db.execute(
        select(Seymour).where(Seymour.id == seymour_id, Seymour.user_id == user.id)
    )
    seymour = result.scalar_one_or_none()
    if not seymour:
        raise HTTPException(status_code=404, detail="Seymour not found")
    
    await db.delete(seymour)
    await db.commit()
    await invalidate_global_stats(redis)
    await invalidate_user_seymours(redis, user.id)
    return {"detail": "Deleted"}


@router.delete("/")
async def delete_all_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    await db.execute(delete(Seymour).where(Seymour.user_id == user.id))
    await db.commit()
    await invalidate_global_stats(redis)
    await invalidate_user_seymours(redis, user.id)
    return {"detail": "All seymours deleted"}

