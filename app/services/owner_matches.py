"""Who owns a colour close to a given hex.

Two questions, one primitive: the holdings near the *searched* colour
(find_owner_matches) and the tiered owners of each *catalog* colour
(tier_users). Both are ΔE from one hex to every owned hex.

This used to run off precomputed ΔE≤10 neighbourhood tables, one per catalog
target, which made the searched colour unanswerable -- it has no precomputed
neighbourhood. Measuring directly needs neither: each owned colour's LAB is
gathered once per snapshot (OwnerHexes.labs), so the whole owner table is one
dense subtraction and one norm.
"""
import numpy as np

from app.services.compute import get_lab
from app.services.matching import TIER_THRESHOLDS, get_tier
from app.services.owner_hexes import OwnerHexes

MAX_DELTA_E = 2.0

# Anything past the last tier is not shown at all, so it is never worth ranking.
TIER_MAX_DELTA_E = max(high for _, high in TIER_THRESHOLDS.values())


def _delta_es(owners: OwnerHexes, hex_code: str) -> np.ndarray:
    return np.linalg.norm(owners.labs - get_lab(hex_code), axis=1)


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


def _rows_for(
    owners: OwnerHexes, pieces: list[int] | None, user_id: int | None = None
) -> np.ndarray:
    rows = np.arange(len(owners.hexes))
    if pieces:
        rows = rows[np.isin(np.frombuffer(owners.pieces, dtype=np.uint8)[rows], pieces)]
    if user_id is not None:
        index = next((i for i, u in enumerate(owners.users) if u[0] == user_id), None)
        # An unknown user owns nothing, rather than everything.
        if index is None:
            return rows[:0]
        rows = rows[np.frombuffer(owners.idx, dtype=np.uint32)[rows] == index]
    return rows


def owned_colours(
    owners: OwnerHexes, pieces: list[int] | None = None, user_id: int | None = None
) -> np.ndarray:
    """The distinct colours held, ascending. Scoped to one user when given."""
    hexes = np.frombuffer(owners.hexes, dtype=np.uint32)
    return np.unique(hexes[_rows_for(owners, pieces, user_id)])


def owners_by_hex(
    owners: OwnerHexes, colours: np.ndarray, pieces: list[int] | None = None
) -> dict[int, list[dict]]:
    """Who holds each of `colours`, keyed by colour.

    Unlike find_owner_matches this asks about exact colours rather than a
    radius, so there is no ΔE to rank by and a user collapses to one entry
    carrying every piece they hold it on.

    Sorting the table once and bisecting keeps this independent of how many
    colours are asked about -- callers pass one page, but the table is the whole
    seymours snapshot either way.
    """
    hexes = np.frombuffer(owners.hexes, dtype=np.uint32)
    idx = np.frombuffer(owners.idx, dtype=np.uint32)
    piece_of = np.frombuffer(owners.pieces, dtype=np.uint8)

    rows = _rows_for(owners, pieces)
    order = rows[np.argsort(hexes[rows], kind="stable")]
    sorted_hexes = hexes[order]

    colours = np.asarray(colours, dtype=np.uint32)
    lo = np.searchsorted(sorted_hexes, colours, side="left")
    hi = np.searchsorted(sorted_hexes, colours, side="right")

    result: dict[int, list[dict]] = {}
    for colour, start, end in zip(colours.tolist(), lo.tolist(), hi.tolist()):
        by_user: dict[int, dict] = {}
        for row in order[start:end].tolist():
            user_index = int(idx[row])
            entry = by_user.get(user_index)
            if entry is None:
                _, username, discord_id, avatar_hash = owners.users[user_index]
                entry = by_user[user_index] = {
                    "username": username,
                    "discord_id": str(discord_id),
                    "avatar_hash": avatar_hash,
                    "pieces": set(),
                    "quantity": 0,
                }
            entry["pieces"].add(int(piece_of[row]))
            entry["quantity"] += 1

        for entry in by_user.values():
            entry["pieces"] = sorted(entry["pieces"])
        result[colour] = sorted(by_user.values(), key=lambda o: o["username"])

    return result
