"""Pure filtering/grouping over a user's Seymours.

No database or cache here: everything takes the already-loaded rows. Splitting
this out is what keeps app/services/seymours.py to orchestration.
"""
from collections import Counter
from typing import Optional

from app.models import Seymour
from app.services.compute import (
    FADE_DYES,
    ITEM_TO_INFO,
    KEY_ITEM_HEXES,
    SETS,
    find_armor_sets,
    find_fade_dye_sets,
    find_incomplete_armor_sets,
    find_incomplete_dye_sets,
    find_matching_seymours,
)
from app.services.matching import (
    TIER_THRESHOLDS,
    dye_label_for_hex,
    list_set_names,
    set_name_for_hex,
)
from app.services.stats import is_star, _contains_word

def _build_result(s: Seymour, match: Optional[dict] = None) -> dict:
    return {
        "id": s.id,
        "item_name": s.item_name,
        "hex_color": s.hex_color,
        "match": match
    }


def _is_in_tier(de: float, tier: str) -> bool:
    min_de, max_de = TIER_THRESHOLDS[tier]
    return min_de <= de < max_de


def _get_matches(
    seymour_list: list[tuple],
    tier: Optional[str] = None,
    sub_filter: Optional[str] = None,
    include_dyes: bool = False,
) -> dict:
    sub_filter_set = set(sub_filter.split(",")) if sub_filter else None
    matches = find_matching_seymours(
        seymour_list, KEY_ITEM_HEXES, threshold=10.0, best_only=True, include_dyes=include_dyes
    )
    match_info = {}
    for match in matches:
        user_hex, piece_idx = match[0]
        target_hex = match[1][0]
        de = match[2]

        if tier and not _is_in_tier(de, tier):
            continue

        set_name = set_name_for_hex(target_hex)
        if set_name is None and include_dyes:
            set_name = dye_label_for_hex(target_hex, with_stage=True)
        if set_name is None:
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


def _armor_set_results(
    seymours: list[Seymour],
    seymour_list: list[tuple],
    sub_filter_set: Optional[set],
    incomplete_only: bool,
) -> list[dict]:
    lookup = _build_seymour_lookup(seymours)
    results = []

    if incomplete_only:
        for set_name, owned_pieces, missing_pieces in find_incomplete_armor_sets(seymour_list):
            if sub_filter_set and set_name not in sub_filter_set:
                continue
            missing = [p[1] for p in missing_pieces if p[1] >= 0]
            for item_name, piece_idx, user_hex, de in owned_pieces:
                key = (user_hex, piece_idx)
                if key in lookup:
                    results.append(_build_result(lookup[key][0], {
                        "target_name": set_name,
                        "target_hex": ITEM_TO_INFO.get(item_name, {}).get("hex", user_hex),
                        "delta_e": round(de, 2),
                        "missing": missing
                    }))
        return results

    for set_name, pieces in find_armor_sets(seymour_list):
        if sub_filter_set and set_name not in sub_filter_set:
            continue
        for item_name, piece_idx, user_hex, de in pieces:
            key = (user_hex, piece_idx)
            if key in lookup:
                results.append(_build_result(lookup[key][0], {
                    "target_name": set_name,
                    "target_hex": ITEM_TO_INFO.get(item_name, {}).get("hex", user_hex),
                    "delta_e": round(de, 2)
                }))
    return results


def _dye_set_results(
    seymours: list[Seymour],
    seymour_list: list[tuple],
    sub_filter_set: Optional[set],
    incomplete_only: bool,
) -> list[dict]:
    lookup = _build_seymour_lookup(seymours)
    results = []

    if incomplete_only:
        for dye_name, stage, owned_pieces, missing_pieces in find_incomplete_dye_sets(seymour_list):
            if sub_filter_set and dye_name not in sub_filter_set:
                continue
            target_hex = FADE_DYES.get(dye_name, {}).get(stage, "")
            missing = [p for p in missing_pieces if p >= 0]
            for piece_idx, user_hex, de in owned_pieces:
                key = (user_hex, piece_idx)
                if key in lookup:
                    results.append(_build_result(lookup[key][0], {
                        "target_name": f"{dye_name} Stage {stage}",
                        "target_hex": target_hex,
                        "delta_e": round(de, 2),
                        "missing": missing
                    }))
        return results

    for dye_name, stage, pieces in find_fade_dye_sets(seymour_list):
        if sub_filter_set and dye_name not in sub_filter_set:
            continue
        target_hex = FADE_DYES.get(dye_name, {}).get(stage, "")
        for piece_idx, user_hex, de in pieces:
            key = (user_hex, piece_idx)
            if key in lookup:
                results.append(_build_result(lookup[key][0], {
                    "target_name": f"{dye_name} Stage {stage}",
                    "target_hex": target_hex,
                    "delta_e": round(de, 2)
                }))
    return results


def _custom_set_results(seymours: list[Seymour], custom_sets: Optional[list]) -> list[dict]:
    lookup = _build_seymour_lookup(seymours)
    results = []
    for set_name, _avg_de, pieces in custom_sets or []:
        for piece_idx, hex_color, piece_de in pieces:
            key = (hex_color, piece_idx)
            if key in lookup:
                results.append(_build_result(lookup[key][0], {
                    "target_name": set_name,
                    "target_hex": hex_color,
                    "delta_e": round(piece_de, 2)
                }))
    return results


def filter_with_matches(
    seymours: list[Seymour],
    filter_type: str,
    sub_filter: Optional[str] = None,
    incomplete_only: bool = False,
    include_dyes: bool = False,
    custom_sets_cache: Optional[list] = None,
) -> list[dict]:
    seymour_list = [(s.hex_color, s.item_name) for s in seymours]
    sub_filter_set = set(sub_filter.split(",")) if sub_filter else None

    if filter_type == "stars":
        return [_build_result(s) for s in seymours if is_star(s.hex_color)]

    if filter_type == "words":
        return [_build_result(s) for s in seymours if _contains_word(s.hex_color.upper())]

    if filter_type == "dupes":
        counts = Counter(seymour_list)
        return [_build_result(s) for s in seymours if counts[(s.hex_color, s.item_name)] > 1]

    if filter_type in TIER_THRESHOLDS or (filter_type == "all" and sub_filter):
        tier = filter_type if filter_type in TIER_THRESHOLDS else None
        match_info = _get_matches(seymour_list, tier, sub_filter, include_dyes)
        return [
            _build_result(s, match_info[key])
            for s in seymours
            if (key := (s.hex_color, s.item_name)) in match_info
        ]

    if filter_type == "sets":
        return _armor_set_results(seymours, seymour_list, sub_filter_set, incomplete_only)

    if filter_type == "dyes":
        return _dye_set_results(seymours, seymour_list, sub_filter_set, incomplete_only)

    if filter_type == "custom":
        return _custom_set_results(seymours, custom_sets_cache)

    return [_build_result(s) for s in seymours]


def compute_available_filters(
    seymour_list: list[tuple], filter_type: str, incomplete_only: bool, include_dyes: bool = False
) -> list[str]:
    if filter_type == "sets":
        return list_set_names()

    if filter_type == "dyes":
        return sorted(FADE_DYES.keys())

    if filter_type in TIER_THRESHOLDS or filter_type == "all":
        tier = filter_type if filter_type in TIER_THRESHOLDS else None
        match_info = _get_matches(seymour_list, tier=tier, include_dyes=include_dyes)
        return sorted({m["target_name"] for m in match_info.values()})

    return []


def sort_results(results: list[dict], sort: str, filter_type: str) -> list[dict]:
    if sort not in ("asc", "desc"):
        return results

    reverse = sort == "desc"

    if filter_type in ("sets", "dyes", "custom"):
        group_avgs = {}
        for r in results:
            name = r.get("match", {}).get("target_name", "")
            de = r.get("match", {}).get("delta_e")
            group_avgs.setdefault(name, [])
            if de is not None:
                group_avgs[name].append(de)

        for name, vals in group_avgs.items():
            group_avgs[name] = sum(vals) / len(vals) if vals else float("inf")

        return sorted(
            results,
            key=lambda r: group_avgs.get(r.get("match", {}).get("target_name", ""), float("inf")),
            reverse=reverse,
        )

    return sorted(
        results,
        key=lambda r: r.get("match", {}).get("delta_e") if r.get("match") else float("inf"),
        reverse=reverse,
    )
