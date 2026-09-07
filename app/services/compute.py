import numpy as np
import json
import os
from collections import Counter
from heapq import heapify, heappop, heappush
from itertools import chain
from scipy.spatial import cKDTree

from app.pieces import PIECE_COUNT, Piece, SLOT_TO_PIECE

def _build_lab_table():
    #Makes mapping of RGB -> LAB
    rgb = np.arange(0x1000000, dtype=np.uint32)
    r = ((rgb >> 16) & 0xFF).astype(np.float32) / 255.0
    g = ((rgb >> 8) & 0xFF).astype(np.float32) / 255.0
    b = (rgb & 0xFF).astype(np.float32) / 255.0
    
    def to_linear(c):
        mask = c > 0.04045
        c[mask] = ((c[mask] + 0.055) / 1.055) ** 2.4
        c[~mask] /= 12.92
        return c
    
    r, g, b = to_linear(r), to_linear(g), to_linear(b)
    
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    
    del r, g, b
    
    def f(t):
        mask = t > 0.008856
        t[mask] **= (1/3)
        t[~mask] = 7.787 * t[~mask] + 16/116
        return t
    
    fx, fy, fz = f(x), f(y), f(z)
    
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b_out = 200 * (fy - fz)
    
    return np.column_stack([L, a, b_out]).astype(np.float32)

ALL_LAB = _build_lab_table()


def seymour_converter(list_of_seymours):
    return [(i.hex_color, i.item_name.value) for i in list_of_seymours]

def hex_to_int(hex_str):
    return int(hex_str, 16)

def get_lab(hex_str):
    return ALL_LAB[hex_to_int(hex_str)]

def delta_e(lab1, lab2):
    return np.sqrt(np.sum((lab1 - lab2) ** 2, axis=-1))

def _group_by_piece(seymours):
    by_piece = {}
    for hex_code, piece in seymours:
        by_piece.setdefault(piece, []).append(hex_code)
    return by_piece

def _hex_list_to_ints(hex_list):
    return np.array([int(h, 16) for h in hex_list], dtype=np.uint32)

def _get_piece_tree_and_hexes(key_item_hexes, include_dyes):
    """Returns (trees, piece_hexes) using pre-built if possible, else builds new."""
    if key_item_hexes is KEY_ITEM_HEXES:
        if include_dyes:
            return ALL_PIECE_TREES, ALL_PIECE_HEXES
        return KEY_PIECE_TREES, KEY_PIECE_HEXES
    
    if key_item_hexes is FADE_DYE_HEXES and not include_dyes:
        trees = [cKDTree(ALL_LAB[_hex_list_to_ints(UNIQUE_DYE_HEXES)]) if UNIQUE_DYE_HEXES else None] * 4
        piece_hexes = [UNIQUE_DYE_HEXES] * 4
        return trees, piece_hexes
    
    target_hexes = list(key_item_hexes)
    if include_dyes:
        target_hexes.extend(FADE_DYE_HEXES)
    target_by_piece = _group_by_piece(target_hexes)
    
    piece_hexes = [target_by_piece.get(p, []) for p in range(PIECE_COUNT)]
    trees = []
    for hexes in piece_hexes:
        if hexes:
            trees.append(cKDTree(ALL_LAB[_hex_list_to_ints(hexes)]))
        else:
            trees.append(None)
    return trees, piece_hexes


def _query_best_only(tree, user_labs, user_hexes, target_hexes, piece, threshold):
    dists, indices = tree.query(user_labs, k=1)
    keep = np.flatnonzero(dists < threshold)
    return [
        ((user_hexes[u], piece), (target_hexes[t], piece), d)
        for u, t, d in zip(keep.tolist(), indices[keep].tolist(), dists[keep].tolist())
    ]


def _query_all_matches(tree, user_labs, user_hexes, target_labs, target_hexes, piece, threshold):
    """Every (user, target) pair within threshold, as (user, target, delta_e) triples.

    Flattened rather than looped per user: the loop made one tiny np.linalg.norm
    call per user seymour, where a single batched one covers the whole piece.
    _expand keeps the pairs in the same order the loop emitted them.
    """
    (users, targets), _ = _expand(
        [np.arange(len(user_hexes), dtype=np.int32)],
        tree.query_ball_point(user_labs, threshold),
    )
    if not users.size:
        return []

    dists = _row_norms(user_labs[users] - target_labs[targets])
    return [
        ((user_hexes[u], piece), (target_hexes[t], piece), d)
        for u, t, d in zip(users.tolist(), targets.tolist(), dists.tolist())
    ]


def find_matching_seymours(user_seymours, key_item_hexes, threshold=10.0, best_only=False, include_dyes=False):
    """
    Finds user seymours within delta E threshold of target seymours (same piece type).
    Returns list of (user_seymour, target_seymour, delta_e_value).
    If best_only=True, returns only the closest match per user seymour.
    If include_dyes=True, also matches against fade dye hexes.
    """
    trees, piece_hexes = _get_piece_tree_and_hexes(key_item_hexes, include_dyes)
    user_by_piece = _group_by_piece(user_seymours)
    results = []
    
    for piece in range(PIECE_COUNT):
        user_hexes = user_by_piece.get(piece)
        target_hexes = piece_hexes[piece]
        tree = trees[piece]
        if not user_hexes or not target_hexes or tree is None:
            continue
        
        user_labs = ALL_LAB[_hex_list_to_ints(user_hexes)]
        
        if best_only:
            results.extend(_query_best_only(tree, user_labs, user_hexes, target_hexes, piece, threshold))
        else:
            target_labs = ALL_LAB[_hex_list_to_ints(target_hexes)]
            results.extend(_query_all_matches(tree, user_labs, user_hexes, target_labs, target_hexes, piece, threshold))
    
    return results

def _load_item_data():
    with open(os.path.join(os.path.dirname(__file__), '..', 'static', 'data.json'), 'r') as f:
        data = json.load(f)
    
    sets = data['sets']
    item_to_info = {}
    hex_to_items = {}
    key_item_hexes = []
    
    # Build item_name -> set_name mapping
    item_to_set = {}
    for set_name, item_names in sets.items():
        for item_name in item_names:
            item_to_set[item_name] = set_name
    
    for category in data['categories'].values():
        for item in category:
            name, hex_code, pieces = item['name'], item['hex'], item['pieces']
            item_to_info[name] = {'hex': hex_code, 'pieces': [SLOT_TO_PIECE[p] for p in pieces]}
            hex_to_items.setdefault(hex_code, []).append(name)
            for p in pieces:
                key_item_hexes.append((hex_code, SLOT_TO_PIECE[p]))

    # Several catalog items can share one hex -- Pure Blue and Lapis Armor 3p are
    # both 0000FF. key_item_for_hex takes the first, so a set member has to lead
    # or the set is unreachable from every hex-keyed label and sub-filter; without
    # this the winner is just data.json's category order, which the mod controls.
    for names in hex_to_items.values():
        names.sort(key=lambda name: name not in item_to_set)


    # Fade dyes: {dye_name: {stage: hex}}
    fade_dyes = {}
    fade_dye_hexes = []
    hex_to_dye_stage = {}
    for stage_name, hex_code in data.get('FADE_DYES', {}).items():
        dye_name, stage = stage_name.rsplit(' - Stage ', 1)
        stage = int(stage)
        fade_dyes.setdefault(dye_name, {})[stage] = hex_code
        hex_to_dye_stage.setdefault(hex_code, []).append((dye_name, stage))
        for piece in range(PIECE_COUNT):
            fade_dye_hexes.append((hex_code, piece))
    
    hex_words = set(w.upper() for w in data.get('HEX_WORDS', []))
    
    return sets, item_to_info, hex_to_items, key_item_hexes, fade_dyes, fade_dye_hexes, hex_to_dye_stage, hex_words, item_to_set

SETS, ITEM_TO_INFO, HEX_TO_ITEMS, KEY_ITEM_HEXES, FADE_DYES, FADE_DYE_HEXES, HEX_TO_DYE_STAGE, HEX_WORDS, ITEM_TO_SET = _load_item_data()

UNIQUE_KEY_HEXES = list(dict.fromkeys(h for h, _ in KEY_ITEM_HEXES))
KEY_ITEM_LABS = ALL_LAB[_hex_list_to_ints(UNIQUE_KEY_HEXES)]
KEY_ITEM_TREE = cKDTree(KEY_ITEM_LABS)

UNIQUE_DYE_HEXES = list(dict.fromkeys(h for h, _ in FADE_DYE_HEXES))
DYE_LABS = ALL_LAB[_hex_list_to_ints(UNIQUE_DYE_HEXES)]

UNIQUE_ALL_HEXES = list(dict.fromkeys(UNIQUE_KEY_HEXES + UNIQUE_DYE_HEXES))
ALL_ITEM_LABS = ALL_LAB[_hex_list_to_ints(UNIQUE_ALL_HEXES)]
ALL_ITEM_TREE = cKDTree(ALL_ITEM_LABS)

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'static')

def _load_precomputed_matches():
    keys_idx_path = os.path.join(STATIC_DIR, 'hex_matches_keys.npy')
    keys_de_path = os.path.join(STATIC_DIR, 'hex_matches_keys_de.npy')
    all_idx_path = os.path.join(STATIC_DIR, 'hex_matches_all.npy')
    all_de_path = os.path.join(STATIC_DIR, 'hex_matches_all_de.npy')
    
    if all(os.path.exists(p) for p in [keys_idx_path, keys_de_path, all_idx_path, all_de_path]):
        return (
            np.load(keys_idx_path),
            np.load(keys_de_path),
            np.load(all_idx_path),
            np.load(all_de_path),
        )
    return None, None, None, None

HEX_MATCH_KEYS_IDX, HEX_MATCH_KEYS_DE, HEX_MATCH_ALL_IDX, HEX_MATCH_ALL_DE = _load_precomputed_matches()
USE_PRECOMPUTED = HEX_MATCH_KEYS_IDX is not None

def _build_hex_to_pieces():
    mapping = {}
    for hex_code, piece_idx in KEY_ITEM_HEXES:
        mapping.setdefault(hex_code, set()).add(piece_idx)
    for hex_code in UNIQUE_DYE_HEXES:
        mapping[hex_code] = set(Piece)
    return mapping

HEX_TO_PIECES = _build_hex_to_pieces()

def _build_piece_trees(hexes_with_pieces, include_dyes=False):
    piece_hexes = [[] for _ in range(PIECE_COUNT)]
    for hex_code, piece_idx in hexes_with_pieces:
        piece_hexes[piece_idx].append(hex_code)
    if include_dyes:
        for hex_code in UNIQUE_DYE_HEXES:
            for p in range(PIECE_COUNT):
                piece_hexes[p].append(hex_code)
    piece_hexes = [list(dict.fromkeys(h)) for h in piece_hexes]
    piece_labs = [ALL_LAB[_hex_list_to_ints(h)] if h else np.empty((0, 3), dtype=np.float32) for h in piece_hexes]
    piece_trees = [cKDTree(labs) if len(labs) > 0 else None for labs in piece_labs]
    return piece_hexes, piece_labs, piece_trees

KEY_PIECE_HEXES, KEY_PIECE_LABS, KEY_PIECE_TREES = _build_piece_trees(KEY_ITEM_HEXES, include_dyes=False)
ALL_PIECE_HEXES, ALL_PIECE_LABS, ALL_PIECE_TREES = _build_piece_trees(KEY_ITEM_HEXES, include_dyes=True)


def _top_matches_for_pieces(
    hex_code: str, limit: int, include_dyes: bool, pieces: list[int]
) -> list[tuple[str, float]]:
    """Nearest catalog colours available on any of `pieces`.

    Per-piece trees rather than filtering the global tree's results: a slot with
    few items would otherwise need an unbounded k before enough of them survived.
    """
    piece_hexes, trees = (
        (ALL_PIECE_HEXES, ALL_PIECE_TREES) if include_dyes else (KEY_PIECE_HEXES, KEY_PIECE_TREES)
    )
    lab = get_lab(hex_code)
    best: dict[str, float] = {}

    for piece in pieces:
        if not 0 <= piece < PIECE_COUNT or trees[piece] is None:
            continue
        hexes = piece_hexes[piece]
        k = min(limit, len(hexes))
        dists, indices = trees[piece].query(lab, k=k)
        if k == 1:
            dists, indices = [dists], [indices]

        for i, d in zip(indices, dists):
            target, distance = hexes[i], float(d)
            if distance < best.get(target, float("inf")):
                best[target] = distance

    return sorted(best.items(), key=lambda kv: (kv[1], kv[0]))[:limit]


def find_top_hex_matches(
    hex_code: str, limit: int = 3, include_dyes: bool = False, pieces: list[int] | None = None
) -> list[tuple[str, float]]:
    if pieces:
        return _top_matches_for_pieces(hex_code, limit, include_dyes, pieces)

    if USE_PRECOMPUTED and limit == 1:
        idx_arr, de_arr, hex_list = (HEX_MATCH_ALL_IDX, HEX_MATCH_ALL_DE, UNIQUE_ALL_HEXES) if include_dyes else (HEX_MATCH_KEYS_IDX, HEX_MATCH_KEYS_DE, UNIQUE_KEY_HEXES)
        i = int(hex_code, 16)
        return [(hex_list[idx_arr[i]], float(de_arr[i]))]
    
    hex_list, tree = (UNIQUE_ALL_HEXES, ALL_ITEM_TREE) if include_dyes else (UNIQUE_KEY_HEXES, KEY_ITEM_TREE)
    dists, indices = tree.query(get_lab(hex_code), k=limit)
    if limit == 1:
        return [(hex_list[indices], float(dists))]
    return [(hex_list[i], float(d)) for i, d in zip(indices, dists)]


def find_top_hex_matches_batch(hex_codes: list[str], include_dyes: bool = False) -> dict[str, tuple[str, float]]:
    if not hex_codes:
        return {}
    
    unique_hexes = list(dict.fromkeys(h.upper() for h in hex_codes))
    
    if USE_PRECOMPUTED:
        idx_arr, de_arr, hex_list = (HEX_MATCH_ALL_IDX, HEX_MATCH_ALL_DE, UNIQUE_ALL_HEXES) if include_dyes else (HEX_MATCH_KEYS_IDX, HEX_MATCH_KEYS_DE, UNIQUE_KEY_HEXES)
        ints = _hex_list_to_ints(unique_hexes)
        return {h: (hex_list[idx_arr[i]], float(de_arr[i])) for h, i in zip(unique_hexes, ints)}
    
    hex_list, tree = (UNIQUE_ALL_HEXES, ALL_ITEM_TREE) if include_dyes else (UNIQUE_KEY_HEXES, KEY_ITEM_TREE)
    input_labs = ALL_LAB[_hex_list_to_ints(unique_hexes)]
    dists, indices = tree.query(input_labs, k=1)
    return {h: (hex_list[indices[i]], float(dists[i])) for i, h in enumerate(unique_hexes)}


def _count_user_pieces(seymour_list):
    return Counter((hex_code, piece_idx) for hex_code, piece_idx in seymour_list)

def _build_user_matches(matches):
    user_matches = {}
    for user_s, target_s, de in matches:
        target_hex, piece_idx = target_s
        user_hex = user_s[0]
        for item_name in HEX_TO_ITEMS.get(target_hex, []):
            key = (item_name, piece_idx)
            if key not in user_matches or de < user_matches[key][1]:
                user_matches[key] = (user_hex, de)
    return user_matches

def _resolve_slots(lookup, slots, user_counts):
    """Split the slots a set asks for into owned and missing.

    `slots` is [(lookup_key, tag, piece_idx)]; `tag` is whatever the caller wants
    echoed back. Returns (owned, missing, shortfall) where owned is
    [(tag, piece_idx, user_hex, delta_e)], missing is [(tag, piece_idx)], and
    shortfall is True when one owned piece is claimed by more slots than the user
    holds copies of it -- e.g. two items in a set wanting the same chestplate.
    """
    owned, missing = [], []
    demand = Counter()

    for key, tag, piece_idx in slots:
        match = lookup.get(key)
        if not match:
            missing.append((tag, piece_idx))
            continue
        user_hex, de = match
        demand[(user_hex, piece_idx)] += 1
        owned.append((tag, piece_idx, user_hex, de))

    shortfall = any(user_counts.get(k, 0) < needed for k, needed in demand.items())
    return owned, missing, shortfall


def _armor_slots(item_names):
    """Returns (slots, all_known). A set with an item missing from the catalog can
    never be complete, but its known items still count toward an incomplete one."""
    slots, all_known = [], True
    for item_name in item_names:
        info = ITEM_TO_INFO.get(item_name)
        if not info:
            all_known = False
            continue
        slots.extend(((item_name, piece_idx), item_name, piece_idx) for piece_idx in info['pieces'])
    return slots, all_known


def _dye_slots(dye_name, stage):
    return [((dye_name, stage, piece_idx), piece_idx, piece_idx) for piece_idx in range(PIECE_COUNT)]

def _build_fade_dye_matches(matches):
    """Build lookup: (dye_name, stage, piece_idx) -> (user_hex, delta_e)"""
    dye_matches = {}
    for user_s, target_s, de in matches:
        target_hex, piece_idx = target_s
        user_hex = user_s[0]
        for dye_name, stage in HEX_TO_DYE_STAGE.get(target_hex, []):
            key = (dye_name, stage, piece_idx)
            if key not in dye_matches or de < dye_matches[key][1]:
                dye_matches[key] = (user_hex, de)
    return dye_matches

def find_fade_dye_sets(seymour_list, threshold=5.0):
    """
    Finds complete fade dye sets (all 4 pieces at same stage).
    Returns list of (dye_name, stage, [(piece_idx, user_hex, delta_e), ...])
    """
    matches = find_matching_seymours(seymour_list, FADE_DYE_HEXES, threshold=threshold, best_only=False)
    dye_matches = _build_fade_dye_matches(matches)
    user_counts = _count_user_pieces(seymour_list)
    
    complete_dye_sets = []
    for dye_name, stages in FADE_DYES.items():
        for stage in stages:
            owned, missing, shortfall = _resolve_slots(
                dye_matches, _dye_slots(dye_name, stage), user_counts
            )
            if owned and not missing and not shortfall:
                complete_dye_sets.append(
                    (dye_name, stage, [(p, h, de) for _, p, h, de in owned])
                )

    return complete_dye_sets

def find_armor_sets(seymour_list, threshold=5.0):
    matches = find_matching_seymours(seymour_list, KEY_ITEM_HEXES, threshold=threshold, best_only=False)
    user_matches = _build_user_matches(matches)
    user_counts = _count_user_pieces(seymour_list)
    
    complete_sets = []
    for set_name, item_names in SETS.items():
        slots, all_known = _armor_slots(item_names)
        if not all_known:
            continue
        owned, missing, shortfall = _resolve_slots(user_matches, slots, user_counts)
        if owned and not missing and not shortfall:
            complete_sets.append((set_name, owned))

    return complete_sets

def find_sets(seymour_list, threshold=5.0):
    """Returns (armor_sets, fade_dye_sets)"""
    return find_armor_sets(seymour_list, threshold), find_fade_dye_sets(seymour_list, threshold)


def find_incomplete_armor_sets(seymour_list, threshold=5.0):
    """
    Finds armor sets where user has at least one piece but not all.
    Returns list of (set_name, owned_pieces, missing_pieces)
    """
    matches = find_matching_seymours(seymour_list, KEY_ITEM_HEXES, threshold=threshold, best_only=False)
    user_matches = _build_user_matches(matches)
    user_counts = _count_user_pieces(seymour_list)
    
    incomplete_sets = []
    for set_name, item_names in SETS.items():
        slots, _ = _armor_slots(item_names)
        owned, missing, shortfall = _resolve_slots(user_matches, slots, user_counts)
        if shortfall:
            missing.append(("duplicate_needed", -1))

        if owned and missing:
            incomplete_sets.append((set_name, owned, missing))

    return incomplete_sets


def find_incomplete_dye_sets(seymour_list, threshold=5.0):
    """
    Finds fade dye sets where user has at least one piece but not all 4.
    Returns list of (dye_name, stage, owned_pieces, missing_pieces)
    """
    matches = find_matching_seymours(seymour_list, FADE_DYE_HEXES, threshold=threshold, best_only=False)
    dye_matches = _build_fade_dye_matches(matches)
    user_counts = _count_user_pieces(seymour_list)
    
    incomplete_dye_sets = []
    for dye_name, stages in FADE_DYES.items():
        for stage in stages:
            owned, missing, shortfall = _resolve_slots(
                dye_matches, _dye_slots(dye_name, stage), user_counts
            )
            missing = [tag for tag, _ in missing]
            if shortfall:
                missing.append(-1)

            if owned and missing:
                incomplete_dye_sets.append(
                    (dye_name, stage, [(p, h, de) for _, p, h, de in owned], missing)
                )

    return incomplete_dye_sets


def _calc_avg_centroid_de(labs):
    """Calculate average Delta E from centroid."""
    centroid = np.mean(labs, axis=0)
    return float(np.mean(np.sqrt(np.sum((labs - centroid) ** 2, axis=1))))


def _get_piece_labs(seymour_list):
    by_piece = _group_by_piece(seymour_list)
    pieces = [list(set(by_piece.get(i, []))) for i in range(PIECE_COUNT)]
    if not all(pieces):
        return None, None
    piece_ints = [np.array([int(h, 16) for h in p], dtype=np.uint32) for p in pieces]
    piece_labs = [ALL_LAB[ints] for ints in piece_ints]
    return pieces, piece_labs


def _row_norms(diff):
    return np.sqrt(diff[:, 0] ** 2 + diff[:, 1] ** 2 + diff[:, 2] ** 2)


def _score_quads(piece_labs, columns):
    """Mean distance-from-centroid for each quadruple, given its four index columns.

    Plain ufuncs over (n,3) arrays rather than an (n,4,3) array plus two
    ndarray.mean calls -- numpy's per-call dispatch dominates the arithmetic.
    The summation order is deliberate: these are float32, so reassociating them
    shifts the last digits and moves ties in _greedy_select. Unrolling per
    component is slower again, trading array ops for numpy scalar extraction.
    Measure before changing: scripts/bench_compute.sh.
    """
    l0, l1, l2, l3 = (labs[column] for labs, column in zip(piece_labs, columns))
    centroids = (l0 + l1 + l2 + l3) * 0.25
    total = _row_norms(l3 - centroids)
    total += _row_norms(l0 - centroids)
    total += _row_norms(l1 - centroids)
    total += _row_norms(l2 - centroids)
    return total * 0.25


def _expand(columns, neighbours):
    """Cross each parent row with its neighbour list, as flat index columns.

    Neighbour lists arrive sorted, so repeating the parents in place keeps the
    candidates in lexicographic (i0, i1, i2, i3) order -- which is the insertion
    order _greedy_select's stable sort breaks ties by.
    """
    counts = np.fromiter(
        (len(n) for n in neighbours), dtype=np.intp, count=len(neighbours)
    )
    child = np.fromiter(
        chain.from_iterable(neighbours), dtype=np.int32, count=int(counts.sum())
    )
    return [np.repeat(column, counts) for column in columns] + [child], counts


def _segment_argmin(values, starts, lengths):
    """Position of the first minimum within each segment. Segments must be non-empty."""
    at_min = np.flatnonzero(
        values == np.repeat(np.minimum.reduceat(values, starts), lengths)
    )
    segments = np.repeat(np.arange(len(starts)), lengths)[at_min]
    return at_min[np.flatnonzero(np.concatenate(([True], segments[1:] != segments[:-1])))]


def _greedy_select(scores, indices, counts, threshold):
    """Positions of the lowest-scoring candidates whose four piece indices are unused.

    Best-first over triples rather than a sort of every candidate. Emitting a set
    consumes its first three piece indices, so a triple yields at most one set and
    only its best still-free candidate need ever be live: ~64k heap entries against
    ~1.4M sorted rows for a 10k inventory. `counts` gives each triple's candidate
    run, contiguous because _expand preserves lexicographic order.

    Same sequence as sorting everything: the heap orders by (score, position), so
    equal scores keep insertion order. Getting that wrong silently selects a
    different, equally scoring set.
    """
    if not scores.size:
        return np.empty(0, dtype=np.intp)

    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    nonempty = counts > 0
    starts, lengths = starts[nonempty], counts[nonempty]

    used = [bytearray(int(size) + 1) for size in indices.max(axis=0)]
    by_score = {}
    cursors = {}

    def next_free(triple):
        """That triple's cheapest remaining candidate whose slot-3 piece is unused."""
        order = by_score.get(triple)
        if order is None:
            start = int(starts[triple])
            stop = start + int(lengths[triple])
            order = by_score[triple] = (
                np.argsort(scores[start:stop], kind="stable") + start
            ).tolist()
            cursors[triple] = 0

        for cursor in range(cursors[triple], len(order)):
            position = order[cursor]
            if scores[position] >= threshold:
                break
            if not used[3][indices[position, 3]]:
                cursors[triple] = cursor + 1
                return position

        cursors[triple] = len(order)
        return None

    heap = [
        (float(scores[position]), position, triple)
        for triple, position in enumerate(
            _segment_argmin(scores, starts, lengths).tolist()
        )
        if scores[position] < threshold
    ]
    heapify(heap)

    selected = []
    while heap:
        _, position, triple = heappop(heap)
        i0, i1, i2, i3 = indices[position].tolist()

        if used[0][i0] or used[1][i1] or used[2][i2]:
            continue
        if used[3][i3]:
            refill = next_free(triple)
            if refill is not None:
                heappush(heap, (float(scores[refill]), refill, triple))
            continue

        for slot, index in enumerate((i0, i1, i2, i3)):
            used[slot][index] = 1
        selected.append(position)

    return np.array(selected, dtype=np.intp)


# Triples per batch at the final expansion. Sized so one batch stays in the
# low hundreds of MB at the densities a full inventory produces.
_TRIPLE_CHUNK = 25_000


def find_custom_sets(seymour_list, max_avg_delta_e=1.0):
    pieces, piece_labs = _get_piece_labs(seymour_list)
    if pieces is None:
        return []
    
    order = np.argsort([len(p) for p in pieces])
    pieces = [pieces[i] for i in order]
    piece_labs = [piece_labs[i] for i in order]
    inv_order = np.argsort(order)
    
    labs0, labs1, labs2, labs3 = piece_labs
    trees = [cKDTree(labs) for labs in piece_labs]
    radius = max_avg_delta_e * 2.0
    
    # Expanded a level at a time as flat index columns rather than by nested
    # loop: the loop made ~64k tiny tree queries and ~64k tiny score calls on a
    # 10k inventory, where a handful of batched ones do the same work.
    (p0, p1), _ = _expand(
        [np.arange(len(labs0), dtype=np.int32)],
        trees[1].query_ball_point(labs0, radius),
    )
    if not p0.size:
        return []

    (t0, t1, t2), _ = _expand(
        [p0, p1],
        trees[2].query_ball_point((labs0[p0] + labs1[p1]) * 0.5, radius),
    )
    if not t0.size:
        return []

    # Only the last level is chunked -- it is where the fan-out lands: a 10k
    # inventory turns 7k pairs into 64k triples but 1.4M quadruples.
    # Kept unfiltered: _greedy_select segments these by triple using `counts`, so
    # dropping over-threshold rows here would break the run offsets. It applies
    # the threshold itself.
    score_blocks = []
    index_blocks = []
    count_blocks = []
    for start in range(0, t0.size, _TRIPLE_CHUNK):
        chunk = [column[start:start + _TRIPLE_CHUNK] for column in (t0, t1, t2)]
        centroids = (labs0[chunk[0]] + labs1[chunk[1]] + labs2[chunk[2]]) / 3

        columns, counts = _expand(chunk, trees[3].query_ball_point(centroids, radius))
        if not columns[3].size:
            continue

        score_blocks.append(_score_quads(piece_labs, columns))
        index_blocks.append(np.stack(columns, axis=1))
        count_blocks.append(counts)

    if not score_blocks:
        return []

    scores = np.concatenate(score_blocks)
    candidate_indices = np.concatenate(index_blocks)
    selected = _greedy_select(
        scores, candidate_indices, np.concatenate(count_blocks), max_avg_delta_e
    )

    results = []
    for position in selected:
        avg_de = scores[position]
        indices = candidate_indices[position].tolist()
        hexes = [pieces[j][indices[j]] for j in range(PIECE_COUNT)]
        labs = np.array([piece_labs[j][indices[j]] for j in range(PIECE_COUNT)])
        centroid = labs.mean(axis=0)
        piece_de = np.linalg.norm(labs - centroid, axis=1)
        
        final_hexes = [hexes[inv_order[j]] for j in range(PIECE_COUNT)]
        final_de = [piece_de[inv_order[j]] for j in range(PIECE_COUNT)]
        
        results.append((f"Custom Set #{len(results) + 1}", float(avg_de),
                       [(j, final_hexes[j], float(final_de[j])) for j in range(PIECE_COUNT)]))
    
    return results
