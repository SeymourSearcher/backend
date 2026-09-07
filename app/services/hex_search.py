"""Colours matching a hex pattern, best catalog match first.

The pattern names a set, not a colour, so the work is: generate that set, look
up each member's distance to the nearest catalog item, and page the result by
distance. Every step is an array operation over colour indices -- the same
0xRRGGBB addressing ALL_LAB and the precomputed match tables already use.

Ownership is an annotation, not a filter, unless owned_only is set. The piece
filter is likewise an ownership-side concern only: ΔE, tier and target name are
always the *global* nearest match, so that turning a slot on cannot silently
restate what a colour is worth. See app/services/hex_pattern.py for the grammar.
"""
import asyncio

import numpy as np
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.services import color_index, compute
from app.services.compute import ALL_LAB
from app.services.hex_pattern import PATTERN_LENGTH, HexPattern, colour_indices, parse_pattern
from app.services.matching import TIER_THRESHOLDS, get_name_for_hex, get_tier
from app.services.owner_hexes import OwnerHexes, get_owner_hexes
from app.services.owner_matches import owned_colours, owners_by_hex

MAX_OFFSET = 10_000


def _catalog(include_dyes: bool):
    """The (nearest-index, ΔE, hex list, tree) quadruple for one catalog scope."""
    if include_dyes:
        return (
            compute.HEX_MATCH_ALL_IDX,
            compute.HEX_MATCH_ALL_DE,
            compute.UNIQUE_ALL_HEXES,
            compute.ALL_ITEM_TREE,
        )
    return (
        compute.HEX_MATCH_KEYS_IDX,
        compute.HEX_MATCH_KEYS_DE,
        compute.UNIQUE_KEY_HEXES,
        compute.KEY_ITEM_TREE,
    )


def _delta_es(candidates: np.ndarray, include_dyes: bool) -> np.ndarray:
    """ΔE from each colour to its nearest catalog item.

    float16 is widened to float32 up front: the cast is lossless, and it keeps
    the compare and the partition off numpy's float16 emulation path.
    """
    _, de_arr, _, tree = _catalog(include_dyes)
    if compute.USE_PRECOMPUTED:
        return de_arr[candidates].astype(np.float32)

    # Same fallback shape as compute.find_top_hex_matches_batch.
    dists, _ = tree.query(ALL_LAB[candidates], k=1, workers=-1)
    return np.asarray(dists, dtype=np.float32)


def _apply_tier(candidates: np.ndarray, de: np.ndarray, tier: str | None):
    """The persisted tier columns are derived from the same float16 ΔE (see
    matching.match_columns), so a colour rounding across a band edge lands here
    exactly where the database already put it. Recomputing exactly would make
    this page disagree with the hex page and the auction stats."""
    if not tier or tier == "all":
        return candidates, de
    low, high = TIER_THRESHOLDS[tier]
    keep = (de >= low) & (de < high)
    return candidates[keep], de[keep]


def _page(candidates: np.ndarray, de: np.ndarray, limit: int, offset: int) -> np.ndarray:
    """The offset..offset+limit colours by (ΔE asc, colour asc).

    candidates ascends, so a *stable* sort keyed on ΔE alone already breaks ties
    by colour. argpartition finds the cut, then only the colours tied with it get
    sorted -- a full argsort of a million-colour pattern is not worth paying for
    fifty rows.
    """
    wanted = offset + limit
    if wanted >= len(candidates):
        order = np.argsort(de, kind="stable")
    else:
        cut = de[np.argpartition(de, wanted - 1)[:wanted]].max()
        window = np.flatnonzero(de <= cut)
        order = window[np.argsort(de[window], kind="stable")]
    return candidates[order[offset:offset + limit]]


def _ordered_index_page(
    pattern: HexPattern, include_dyes: bool, tier: str | None, limit: int, offset: int
) -> tuple[np.ndarray, int]:
    """The unconstrained case, served from the precomputed ΔE ordering.

    Every colour matches, so the answer is color_index's permutation itself --
    already sorted by ΔE, and already tie-broken by colour index because the
    precompute uses a stable argsort. A tier is then a contiguous slice of it.
    """
    if tier and tier != "all":
        selection = color_index.band(*TIER_THRESHOLDS[tier], include_dyes)
        start = selection.lo + offset
        return np.asarray(selection.source[start:start + limit]), selection.count

    order = color_index.ORDER_ALL if include_dyes else color_index.ORDER_KEYS
    return np.asarray(order[offset:offset + limit]), pattern.total


def _search(
    owners: OwnerHexes,
    pattern: HexPattern,
    include_dyes: bool,
    owned_only: bool,
    tier: str | None,
    pieces: list[int] | None,
    limit: int,
    offset: int,
) -> dict:
    unconstrained = pattern.free == PATTERN_LENGTH

    if unconstrained and not owned_only and color_index.HAS_ORDER:
        page, total = _ordered_index_page(pattern, include_dyes, tier, limit, offset)
        return _rows(owners, page, total, pattern.total, include_dyes, pieces, limit, offset)

    if owned_only:
        held = owned_colours(owners, pieces)
        candidates = held if unconstrained else np.intersect1d(
            colour_indices(pattern), held, assume_unique=True
        )
    elif unconstrained:
        # Neither artifact present and nothing to narrow the space with: the
        # 16.7M gather below is affordable, a 16.7M tree query is not.
        if not compute.USE_PRECOMPUTED:
            raise InvalidInputError("Pattern must constrain at least one digit")
        candidates = np.arange(0x1000000, dtype=np.uint32)
    else:
        candidates = colour_indices(pattern)

    de = _delta_es(candidates, include_dyes)
    candidates, de = _apply_tier(candidates, de, tier)
    page = _page(candidates, de, limit, offset)
    return _rows(owners, page, len(candidates), pattern.total, include_dyes, pieces, limit, offset)


def _rows(
    owners: OwnerHexes,
    page: np.ndarray,
    total: int,
    pattern_total: int,
    include_dyes: bool,
    pieces: list[int] | None,
    limit: int,
    offset: int,
) -> dict:
    idx_arr, de_arr, hex_list, tree = _catalog(include_dyes)
    holders = owners_by_hex(owners, page, pieces)

    if len(page) == 0:
        targets, distances = [], []
    elif compute.USE_PRECOMPUTED:
        targets = [hex_list[int(i)] for i in idx_arr[page]]
        distances = de_arr[page].astype(np.float32).tolist()
    else:
        dists, tree_idx = tree.query(ALL_LAB[page], k=1, workers=-1)
        targets = [hex_list[int(i)] for i in np.atleast_1d(tree_idx)]
        distances = np.atleast_1d(dists).tolist()

    items = []
    for colour, target, distance in zip(page.tolist(), targets, distances):
        delta_e = round(float(distance), 2)
        found = holders.get(colour, [])
        items.append({
            "hex_code": f"{colour:06X}",
            "target_hex": target,
            "target_name": get_name_for_hex(target),
            "delta_e": delta_e,
            "tier": get_tier(delta_e),
            "owners": found,
            "owner_count": len(found),
        })

    return {
        "pattern_total": pattern_total,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


async def search_hex_pattern(
    db: AsyncSession,
    redis: Redis,
    raw_pattern: str,
    include_dyes: bool = False,
    owned_only: bool = False,
    tier: str | None = None,
    pieces: list[int] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pattern = parse_pattern(raw_pattern)
    owners = await get_owner_hexes(db, redis)

    result = await asyncio.to_thread(
        _search, owners, pattern, include_dyes, owned_only, tier, pieces, limit, offset
    )
    return {"pattern": pattern.raw, **result}
