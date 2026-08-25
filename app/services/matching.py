"""Hex -> target-item matching and tier bucketing.

Auction rows persist their best match at ingest time in two variants: `_keys`
matches against armour item hexes only, `_all` also includes fade dye stages.
The `include_dyes` query param picks which column pair a request reads, which
is what lets filtering, sorting and pagination happen in SQL instead of Python.

Tier is stored alongside delta_e even though it is derived from it: an equality
predicate on tier plus ORDER BY price is served entirely by a composite index,
whereas a delta_e range predicate forces a filesort. Changing TIER_THRESHOLDS
therefore requires `python -m scripts.backfill_auction_matches --all`.
"""
from app.services.compute import (
    HEX_TO_DYE_STAGE,
    HEX_TO_ITEMS,
    ITEM_TO_INFO,
    ITEM_TO_SET,
    SETS,
    find_top_hex_matches_batch,
)

TIER_THRESHOLDS = {"t1": (0.0, 2.0), "t2": (2.0, 5.0), "t3": (5.0, 10.0)}


def get_tier(delta_e: float | None) -> str | None:
    if delta_e is None:
        return None
    for tier, (min_de, max_de) in TIER_THRESHOLDS.items():
        if min_de <= delta_e < max_de:
            return tier
    return None


def tier_bounds(tier: str) -> tuple[float, float] | None:
    return TIER_THRESHOLDS.get(tier)


def compute_abs_diff(hex1: str | None, hex2: str | None) -> int:
    if not hex1 or not hex2:
        return 0
    r1, g1, b1 = int(hex1[0:2], 16), int(hex1[2:4], 16), int(hex1[4:6], 16)
    r2, g2, b2 = int(hex2[0:2], 16), int(hex2[2:4], 16), int(hex2[4:6], 16)
    return abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)


def get_name_for_hex(target_hex: str | None) -> str | None:
    if not target_hex:
        return None
    item_names = HEX_TO_ITEMS.get(target_hex, [])
    if item_names:
        return ITEM_TO_SET.get(item_names[0], item_names[0])
    dye_info = HEX_TO_DYE_STAGE.get(target_hex, [])
    if dye_info:
        return dye_info[0][0]
    return None


def get_set_target_hexes(set_name: str) -> list[str]:
    item_names = SETS.get(set_name, [])
    return [ITEM_TO_INFO[name]["hex"] for name in item_names if name in ITEM_TO_INFO]


def match_columns(hex_colors: list[str]) -> dict[str, dict[str, object]]:
    """hex -> the four persisted match column values, for sync and backfill."""
    if not hex_colors:
        return {}

    normalised = [h.upper() for h in hex_colors]
    keys = find_top_hex_matches_batch(normalised, include_dyes=False)
    with_dyes = find_top_hex_matches_batch(normalised, include_dyes=True)

    columns = {}
    for hex_color in set(normalised):
        target_keys, de_keys = keys.get(hex_color, (None, None))
        target_all, de_all = with_dyes.get(hex_color, (None, None))
        columns[hex_color] = {
            "target_hex_keys": target_keys,
            "delta_e_keys": de_keys,
            "tier_keys": get_tier(de_keys),
            "target_hex_all": target_all,
            "delta_e_all": de_all,
            "tier_all": get_tier(de_all),
        }
    return columns
