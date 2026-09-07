"""Who owns a colour close to an arbitrary hex.

The hex page used to answer this only through the catalog: it walked the owner
table against the precomputed ΔE≤10 neighbourhood of each top match, so a user
surfaced because they were near an *item* that was near your colour. This
measures against your colour directly.

No KD-tree and no precompute: ALL_LAB is already resident, so the whole owner
table becomes one fancy-index and one norm.
"""
import numpy as np

from app.services.compute import ALL_LAB, get_lab
from app.services.owner_hexes import OwnerHexes

MAX_DELTA_E = 2.0


def _delta_es(owners: OwnerHexes, hex_code: str) -> np.ndarray:
    hexes = np.frombuffer(owners.hexes, dtype=np.uint32)
    return np.linalg.norm(ALL_LAB[hexes] - get_lab(hex_code), axis=1)


def count_owners_within(
    owners: OwnerHexes, hex_code: str, max_delta_e: float = MAX_DELTA_E
) -> int:
    """Distinct users -- not holdings -- with a piece inside the radius."""
    hits = _delta_es(owners, hex_code) < max_delta_e
    return len(np.unique(np.frombuffer(owners.idx, dtype=np.uint32)[hits]))


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
