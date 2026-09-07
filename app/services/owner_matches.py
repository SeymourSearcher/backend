"""Who owns a colour close to a given hex.

Two questions, one primitive: the holdings near the *searched* colour
(find_owner_matches) and the tiered owners of each *catalog* colour
(tier_users). Both are ΔE from one hex to every owned hex.

This used to run off precomputed ΔE≤10 neighbourhood tables, one per catalog
target, which made the searched colour unanswerable -- it has no precomputed
neighbourhood. Measuring directly needs neither: ALL_LAB is already resident, so
the whole owner table is one fancy-index and one norm.
"""
import numpy as np

from app.services.compute import ALL_LAB, get_lab
from app.services.matching import TIER_THRESHOLDS, get_tier
from app.services.owner_hexes import OwnerHexes

MAX_DELTA_E = 2.0

# Anything past the last tier is not shown at all, so it is never worth ranking.
TIER_MAX_DELTA_E = max(high for _, high in TIER_THRESHOLDS.values())


def _delta_es(owners: OwnerHexes, hex_code: str) -> np.ndarray:
    hexes = np.frombuffer(owners.hexes, dtype=np.uint32)
    return np.linalg.norm(ALL_LAB[hexes] - get_lab(hex_code), axis=1)


def _best_per_user(owners: OwnerHexes, hex_code: str, max_delta_e: float) -> dict[int, float]:
    """Each user's closest holding, for users with one inside the radius."""
    des = _delta_es(owners, hex_code)
    idx = np.frombuffer(owners.idx, dtype=np.uint32)

    best: dict[int, float] = {}
    for row in np.flatnonzero(des < max_delta_e).tolist():
        user_index, de = int(idx[row]), float(des[row])
        if de < best.get(user_index, max_delta_e):
            best[user_index] = de
    return best


def tier_users(owners: OwnerHexes, hex_code: str) -> dict[str, list[dict]]:
    """Users bucketed by their best holding's tier, closest first within each.

    A user appears in exactly one tier -- the one their closest piece earns --
    so the three lists partition the owners rather than nesting.
    """
    by_tier: dict[str, list[dict]] = {tier: [] for tier in TIER_THRESHOLDS}

    for user_index, best_de in _best_per_user(owners, hex_code, TIER_MAX_DELTA_E).items():
        tier = get_tier(best_de)
        if tier is None:
            continue
        _, username, discord_id, avatar_hash = owners.users[user_index]
        by_tier[tier].append({
            "username": username,
            "discord_id": str(discord_id),
            "avatar_hash": avatar_hash,
            "delta_e": round(best_de, 2),
        })

    for users in by_tier.values():
        users.sort(key=lambda u: (u["delta_e"], u["username"]))
    return by_tier


def find_owner_matches(
    owners: OwnerHexes, hex_code: str, max_delta_e: float = MAX_DELTA_E
) -> list[dict]:
    """Holdings inside the radius, closest first.

    Grouped by (user, piece, hex) with a quantity: seymours has no unique
    constraint, so a user holding three identical pieces would otherwise take
    three visually identical rows -- most of a page.
    """
    des = _delta_es(owners, hex_code)
    hit_indexes = np.flatnonzero(des < max_delta_e)

    grouped: dict[tuple[int, int, int], dict] = {}
    for row in hit_indexes.tolist():
        user_index = owners.idx[row]
        key = (user_index, owners.pieces[row], owners.hexes[row])
        existing = grouped.get(key)
        if existing is not None:
            existing["quantity"] += 1
            continue

        _, username, discord_id, avatar_hash = owners.users[user_index]
        grouped[key] = {
            "username": username,
            "discord_id": str(discord_id),
            "avatar_hash": avatar_hash,
            "piece": owners.pieces[row],
            "hex_color": f"{owners.hexes[row]:06X}",
            "delta_e": round(float(des[row]), 2),
            "quantity": 1,
        }

    # Username breaks ties so paging over the list is stable.
    return sorted(grouped.values(), key=lambda m: (m["delta_e"], m["username"]))
