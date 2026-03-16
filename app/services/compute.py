import numpy as np
import json
import time
import os
from collections import Counter
from scipy.spatial import cKDTree

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


'''
take a list of hex codes -> all cases of delta e < 10 and the abs
[('FFFF00', 0), ('FFFF00', 1), ('FFFF00', 2), ('FFFF00', 3), ('FFD700', 0), ('FFD700', 1), ('FFD700', 2), ('FFD700', 3), ('E0FCF7', 0), ('E0FCF7', 1)]
Seymour Export - 17581 pieces
17581
[('15E9E8', 3), ('893F15', 3), ('16FAEF', 3), ('EED7A2', 0), ('FAFC2E', 1), ('EF7139', 2), ('1FB8EC', 0), ('2307D3', 1), ('E89617', 3), ('3E23B6', 0)]


find sets for the users hex codes

involving others:
query to find pieces to buy from others to get
duplicates
make a complete set

'''

def all_hex_codes():
    return list(range(0x1000000))

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
    
    piece_hexes = [target_by_piece.get(p, []) for p in range(4)]
    trees = []
    for hexes in piece_hexes:
        if hexes:
            trees.append(cKDTree(ALL_LAB[_hex_list_to_ints(hexes)]))
        else:
            trees.append(None)
    return trees, piece_hexes


def _query_best_only(tree, user_labs, user_hexes, target_hexes, piece, threshold):
    dists, indices = tree.query(user_labs, k=1)
    results = []
    for u in range(len(user_hexes)):
        if dists[u] < threshold:
            results.append(((user_hexes[u], piece), (target_hexes[indices[u]], piece), float(dists[u])))
    return results


def _query_all_matches(tree, user_labs, user_hexes, target_labs, target_hexes, piece, threshold):
    matches = tree.query_ball_point(user_labs, threshold)
    results = []
    for u, target_indices in enumerate(matches):
        if not target_indices:
            continue
        target_indices = np.asarray(target_indices)
        dists = np.linalg.norm(user_labs[u] - target_labs[target_indices], axis=1)
        for t, d in zip(target_indices, dists):
            results.append(((user_hexes[u], piece), (target_hexes[t], piece), float(d)))
    return results


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
    
    for piece in range(4):
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

PIECE_MAP = {'helmet': 0, 'chestplate': 1, 'leggings': 2, 'boots': 3}

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
            item_to_info[name] = {'hex': hex_code, 'pieces': [PIECE_MAP[p] for p in pieces]}
            hex_to_items.setdefault(hex_code, []).append(name)
            for p in pieces:
                key_item_hexes.append((hex_code, PIECE_MAP[p]))
    
    # Fade dyes: {dye_name: {stage: hex}}
    fade_dyes = {}
    fade_dye_hexes = []
    hex_to_dye_stage = {}
    for stage_name, hex_code in data.get('FADE_DYES', {}).items():
        dye_name, stage = stage_name.rsplit(' - Stage ', 1)
        stage = int(stage)
        fade_dyes.setdefault(dye_name, {})[stage] = hex_code
        hex_to_dye_stage.setdefault(hex_code, []).append((dye_name, stage))
        for piece in range(4):
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
        mapping[hex_code] = {0, 1, 2, 3}
    return mapping

HEX_TO_PIECES = _build_hex_to_pieces()

def _build_piece_trees(hexes_with_pieces, include_dyes=False):
    piece_hexes = [[] for _ in range(4)]
    for hex_code, piece_idx in hexes_with_pieces:
        piece_hexes[piece_idx].append(hex_code)
    if include_dyes:
        for hex_code in UNIQUE_DYE_HEXES:
            for p in range(4):
                piece_hexes[p].append(hex_code)
    piece_hexes = [list(dict.fromkeys(h)) for h in piece_hexes]
    piece_labs = [ALL_LAB[_hex_list_to_ints(h)] if h else np.empty((0, 3), dtype=np.float32) for h in piece_hexes]
    piece_trees = [cKDTree(labs) if len(labs) > 0 else None for labs in piece_labs]
    return piece_hexes, piece_labs, piece_trees

KEY_PIECE_HEXES, KEY_PIECE_LABS, KEY_PIECE_TREES = _build_piece_trees(KEY_ITEM_HEXES, include_dyes=False)
ALL_PIECE_HEXES, ALL_PIECE_LABS, ALL_PIECE_TREES = _build_piece_trees(KEY_ITEM_HEXES, include_dyes=True)


def _hex_to_targets(hex_code: str) -> list[tuple[str, int]]:
    return [(hex_code, p) for p in HEX_TO_PIECES.get(hex_code, {0, 1, 2, 3})]


def find_top_hex_matches(hex_code: str, limit: int = 3, include_dyes: bool = False) -> list[tuple[str, float]]:
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


def find_users_near_hex(seymour_list: list[tuple[str, int]], target_hex: str, threshold: float = 10.0) -> list[tuple[str, int, float]]:
    matches = find_matching_seymours(seymour_list, _hex_to_targets(target_hex), threshold=threshold)
    return [(user_hex, piece, de) for (user_hex, piece), _, de in matches]


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

def _get_set_pieces(item_names, user_matches, user_counts):
    pieces = []
    demand = Counter()
    
    for item_name in item_names:
        info = ITEM_TO_INFO.get(item_name)
        if not info:
            return None
        for piece_idx in info['pieces']:
            match = user_matches.get((item_name, piece_idx))
            if not match:
                return None
            user_hex, de = match
            demand[(user_hex, piece_idx)] += 1
            pieces.append((item_name, piece_idx, user_hex, de))
    
    for key, needed in demand.items():
        if user_counts.get(key, 0) < needed:
            return None
    
    return pieces

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
            pieces = []
            demand = Counter()
            complete = True
            
            for piece_idx in range(4):
                match = dye_matches.get((dye_name, stage, piece_idx))
                if not match:
                    complete = False
                    break
                user_hex, de = match
                demand[(user_hex, piece_idx)] += 1
                pieces.append((piece_idx, user_hex, de))
            
            if complete:
                for key, needed in demand.items():
                    if user_counts.get(key, 0) < needed:
                        complete = False
                        break
            
            if complete:
                complete_dye_sets.append((dye_name, stage, pieces))
    
    return complete_dye_sets

def find_armor_sets(seymour_list, threshold=5.0):
    matches = find_matching_seymours(seymour_list, KEY_ITEM_HEXES, threshold=threshold, best_only=False)
    user_matches = _build_user_matches(matches)
    user_counts = _count_user_pieces(seymour_list)
    
    complete_sets = []
    for set_name, item_names in SETS.items():
        pieces = _get_set_pieces(item_names, user_matches, user_counts)
        if pieces:
            complete_sets.append((set_name, pieces))
    
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
        owned = []
        missing = []
        demand = Counter()
        
        for item_name in item_names:
            info = ITEM_TO_INFO.get(item_name)
            if not info:
                continue
            for piece_idx in info['pieces']:
                match = user_matches.get((item_name, piece_idx))
                if match:
                    user_hex, de = match
                    demand[(user_hex, piece_idx)] += 1
                    owned.append((item_name, piece_idx, user_hex, de))
                else:
                    piece_name = ['helmet', 'chestplate', 'leggings', 'boots'][piece_idx]
                    missing.append((item_name, piece_idx, piece_name))
        
        has_demand_issue = any(user_counts.get(k, 0) < v for k, v in demand.items())
        if has_demand_issue:
            missing.append(("duplicate_needed", -1, "duplicate"))
        
        if owned and (missing or has_demand_issue):
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
            owned = []
            missing = []
            demand = Counter()
            
            for piece_idx in range(4):
                match = dye_matches.get((dye_name, stage, piece_idx))
                if match:
                    user_hex, de = match
                    demand[(user_hex, piece_idx)] += 1
                    owned.append((piece_idx, user_hex, de))
                else:
                    piece_name = ['helmet', 'chestplate', 'leggings', 'boots'][piece_idx]
                    missing.append((piece_idx, piece_name))
            
            has_demand_issue = any(user_counts.get(k, 0) < v for k, v in demand.items())
            if has_demand_issue:
                missing.append((-1, "duplicate"))
            
            if owned and (missing or has_demand_issue):
                incomplete_dye_sets.append((dye_name, stage, owned, missing))
    
    return incomplete_dye_sets


def _calc_avg_centroid_de(labs):
    """Calculate average Delta E from centroid."""
    centroid = np.mean(labs, axis=0)
    return float(np.mean(np.sqrt(np.sum((labs - centroid) ** 2, axis=1))))


def _get_piece_labs(seymour_list):
    by_piece = _group_by_piece(seymour_list)
    pieces = [list(set(by_piece.get(i, []))) for i in range(4)]
    if not all(pieces):
        return None, None
    piece_ints = [np.array([int(h, 16) for h in p], dtype=np.uint32) for p in pieces]
    piece_labs = [ALL_LAB[ints] for ints in piece_ints]
    return pieces, piece_labs


def _score_candidates_batch(labs_fixed, labs3_sub):
    n = len(labs3_sub)
    stacked = np.empty((n, 4, 3), dtype=np.float32)
    stacked[:, :3, :] = labs_fixed
    stacked[:, 3, :] = labs3_sub
    centroids = stacked.mean(axis=1, keepdims=True)
    return np.sqrt(((stacked - centroids) ** 2).sum(axis=2)).mean(axis=1)


def _greedy_select(candidates, threshold):
    if not candidates:
        return []
    candidates.sort(key=lambda x: x[0])
    used = [set() for _ in range(4)]
    selected = []
    for c in candidates:
        if c[0] >= threshold:
            break
        indices = c[1:]
        if any(indices[i] in used[i] for i in range(4)):
            continue
        for i in range(4):
            used[i].add(indices[i])
        selected.append(c)
    return selected


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
    
    all_nearby_1 = trees[1].query_ball_point(labs0, radius)
    
    candidates = []
    for i0, nearby_1 in enumerate(all_nearby_1):
        if not nearby_1:
            continue
        
        lab0 = labs0[i0]
        nearby_1 = np.asarray(nearby_1, dtype=np.int32)
        centroids_01 = (lab0 + labs1[nearby_1]) * 0.5
        
        all_nearby_2 = trees[2].query_ball_point(centroids_01, radius)
        
        for j1, i1 in enumerate(nearby_1):
            nearby_2 = all_nearby_2[j1]
            if not nearby_2:
                continue
            
            lab1 = labs1[i1]
            nearby_2 = np.asarray(nearby_2, dtype=np.int32)
            centroids_012 = (lab0 + lab1 + labs2[nearby_2]) / 3
            
            all_nearby_3 = trees[3].query_ball_point(centroids_012, radius)
            
            for j2, i2 in enumerate(nearby_2):
                nearby_3 = all_nearby_3[j2]
                if not nearby_3:
                    continue
                
                nearby_3 = np.asarray(nearby_3, dtype=np.int32)
                labs_fixed = np.array([lab0, lab1, labs2[i2]], dtype=np.float32)
                avg_des = _score_candidates_batch(labs_fixed, labs3[nearby_3])
                
                valid = avg_des < max_avg_delta_e
                for idx in np.where(valid)[0]:
                    candidates.append((avg_des[idx], i0, i1, i2, nearby_3[idx]))
    
    selected = _greedy_select(candidates, max_avg_delta_e)
    
    results = []
    for avg_de, i0, i1, i2, i3 in selected:
        indices = [i0, i1, i2, i3]
        hexes = [pieces[j][indices[j]] for j in range(4)]
        labs = np.array([piece_labs[j][indices[j]] for j in range(4)])
        centroid = labs.mean(axis=0)
        piece_de = np.linalg.norm(labs - centroid, axis=1)
        
        final_hexes = [hexes[inv_order[j]] for j in range(4)]
        final_de = [piece_de[inv_order[j]] for j in range(4)]
        
        results.append((f"Custom Set #{len(results) + 1}", float(avg_de),
                       [(j, final_hexes[j], float(final_de[j])) for j in range(4)]))
    
    return results

if __name__ == "__main__":
    import sys
    import os
    # Add backend to path so absolute imports work
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    
    seymours = json.load(open(r"C:\Users\hexay\SeymourSearcher\seymour_list.json"))
    target = [('D91E41', 1)]

    matches = find_matching_seymours(seymours, target, threshold=10.0, best_only=True)
    print(matches)

