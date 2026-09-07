"""Owned colours matching a hex pattern, best catalog match first.

The pattern names a set of colours, but only the ones somebody actually holds
are worth a row -- a pattern like 21ZZZZ denotes sixteen colours and the odds
that any of them exists are slim. So the search filters the seymours snapshot by
the pattern rather than enumerating what the pattern denotes, which is both
smaller (~10^5 against 10^7) and free of any special case for a pattern that
constrains nothing.

`mine_only` narrows that to the caller's own holdings. It needs a logged-in
caller, so an anonymous request stays database-wide.

The piece filter is an ownership-side concern only: ΔE, tier and target name are
always the *global* nearest match, so turning a slot on cannot silently restate
what a colour is worth. See app/services/hex_pattern.py for the grammar.
"""
import asyncio

import numpy as np
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import compute
from app.services.compute import ALL_LAB
from app.services.hex_pattern import HexPattern, matches, parse_pattern
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


def _nearest(colours: np.ndarray, include_dyes: bool) -> tuple[list[str], np.ndarray]:
    """Each colour's nearest catalog item and the ΔE to it.

    float16 is widened to float32: the cast is lossless, and it keeps the
    compare and the partition off numpy's float16 emulation path.
    """
    idx_arr, de_arr, hex_list, tree = _catalog(include_dyes)
    if len(colours) == 0:
        return [], np.empty(0, dtype=np.float32)

    if compute.USE_PRECOMPUTED:
        return (
            [hex_list[int(i)] for i in idx_arr[colours]],
            de_arr[colours].astype(np.float32),
        )

    # Same fallback shape as compute.find_top_hex_matches_batch.
    dists, tree_idx = tree.query(ALL_LAB[colours], k=1, workers=-1)
    return (
        [hex_list[int(i)] for i in np.atleast_1d(tree_idx)],
        np.atleast_1d(dists).astype(np.float32),
    )


def _apply_tier(colours: np.ndarray, de: np.ndarray, tier: str | None):
    """The persisted tier columns are derived from the same float16 ΔE (see
    matching.match_columns), so a colour rounding across a band edge lands here
    exactly where the database already put it. Recomputing exactly would make
    this page disagree with the hex page and the auction stats."""
    if not tier or tier == "all":
        return colours, de
    low, high = TIER_THRESHOLDS[tier]
    keep = (de >= low) & (de < high)
    return colours[keep], de[keep]


def _page(colours: np.ndarray, de: np.ndarray, limit: int, offset: int) -> np.ndarray:
    """The offset..offset+limit colours by (ΔE asc, colour asc).

    `colours` ascends, so a *stable* sort keyed on ΔE alone already breaks ties
    by colour. argpartition finds the cut, then only the colours tied with it get
    sorted -- a full argsort is not worth paying for one page.
    """
    wanted = offset + limit
    if wanted >= len(colours):
        order = np.argsort(de, kind="stable")
    else:
        cut = de[np.argpartition(de, wanted - 1)[:wanted]].max()
        window = np.flatnonzero(de <= cut)
        order = window[np.argsort(de[window], kind="stable")]
    return colours[order[offset:offset + limit]]


def _search(
    owners: OwnerHexes,
    pattern: HexPattern,
    include_dyes: bool,
    tier: str | None,
    pieces: list[int] | None,
    viewer_id: int | None,
    mine_only: bool,
    limit: int,
    offset: int,
) -> dict:
    held = owned_colours(owners, pieces, viewer_id if mine_only else None)
    candidates = held[matches(pattern, held)]

    # Which of them are the caller's, for the badge. Already the whole candidate
    # set when mine_only is on, so only worth a second pass when it is not.
    mine = (
        set(held.tolist())
        if mine_only
        else set(owned_colours(owners, pieces, viewer_id).tolist())
        if viewer_id is not None
        else set()
    )

    _, de = _nearest(candidates, include_dyes)
    candidates, de = _apply_tier(candidates, de, tier)
    page = _page(candidates, de, limit, offset)

    targets, distances = _nearest(page, include_dyes)
    holders = owners_by_hex(owners, page, pieces)

    items = []
    for colour, target, distance in zip(page.tolist(), targets, distances.tolist()):
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
            "owned_by_me": colour in mine,
        })

    return {
        "pattern_total": pattern.total,
        "total": len(candidates),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


async def search_hex_pattern(
    db: AsyncSession,
    redis: Redis,
    raw_pattern: str,
    include_dyes: bool = False,
    mine_only: bool = False,
    tier: str | None = None,
    pieces: list[int] | None = None,
    limit: int = 10,
    offset: int = 0,
    user_id: int | None = None,
) -> dict:
    pattern = parse_pattern(raw_pattern)
    owners = await get_owner_hexes(db, redis)

    # Anonymous callers have no holdings to narrow to, so the flag is ignored
    # rather than refused -- the toggle is simply unavailable when logged out.
    scoped = mine_only and user_id is not None

    result = await asyncio.to_thread(
        _search, owners, pattern, include_dyes, tier, pieces, user_id, scoped, limit, offset
    )
    return {"pattern": pattern.raw, "mine_only": scoped, **result}
