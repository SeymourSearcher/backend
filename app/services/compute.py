import numpy as np
import json
import time
import os
from collections import Counter

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

def find_matching_seymours(user_seymours, key_item_hexes, threshold=10.0, best_only=False):
    """
    Finds user seymours within delta E threshold of target seymours (same piece type).
    Returns list of (user_seymour, target_seymour, delta_e_value).
    If best_only=True, returns only the closest match per user seymour.
    """
    user_by_piece = _group_by_piece(user_seymours)
    target_by_piece = _group_by_piece(key_item_hexes)
    
    threshold_sq = threshold * threshold
    results = []
    
    for piece in range(4):
        user_hexes = user_by_piece.get(piece)
        target_hexes = target_by_piece.get(piece)
        if not user_hexes or not target_hexes:
            continue
        
        user_labs = ALL_LAB[_hex_list_to_ints(user_hexes)]
        #Cache target labs for 2: key_item_hexes + dyes
        target_labs = ALL_LAB[_hex_list_to_ints(target_hexes)]
        
        diff = user_labs[:, np.newaxis, :] - target_labs[np.newaxis, :, :]
        dist_sq = np.einsum('ijk,ijk->ij', diff, diff)
        
        if best_only:
            min_idx = np.argmin(dist_sq, axis=1)
            min_dist_sq = dist_sq[np.arange(len(user_hexes)), min_idx]
            mask = min_dist_sq < threshold_sq
            results.extend(
                ((user_hexes[u], piece), (target_hexes[min_idx[u]], piece), float(np.sqrt(min_dist_sq[u])))
                for u in np.where(mask)[0]
            )
        else:
            ui, ti = np.where(dist_sq < threshold_sq)
            distances = np.sqrt(dist_sq[ui, ti])
            results.extend(
                ((user_hexes[u], piece), (target_hexes[t], piece), float(d))
                for u, t, d in zip(ui, ti, distances)
            )
    
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

if __name__ == "__main__":
    import sys
    import os
    # Add backend to path so absolute imports work
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    
    from app.services.parser import parse_seymour_file

    with open(r"C:\Users\Hexay\Downloads\AllyMe's Export 2.9.2026.txt", 'r') as f:
        content = f.read()
        parsed, skipped = parse_seymour_file(content)
        seymour_list = seymour_converter(parsed)
        print(len(seymour_list))
        print(seymour_list[:10])    
    matches = find_matching_seymours(seymour_list, KEY_ITEM_HEXES, threshold=5.0, best_only=True)
    print(f"\nFound {len(matches)} matches with delta E < 5")
    for user_s, target_s, de in matches[:10]:
        print(f"  {user_s} -> {target_s} (ΔE={de:.2f})")
    
    armor_sets, dye_sets = find_sets(seymour_list, threshold=5.0)
    
    print(f"\nFound {len(armor_sets)} complete armor sets:")
    for set_name, pieces in armor_sets[:10]:
        max_de = max(p[3] for p in pieces)
        print(f"  {set_name} (worst ΔE={max_de:.2f})")
        for item_name, piece_idx, user_hex, de in pieces:
            piece_name = ['helmet', 'chestplate', 'leggings', 'boots'][piece_idx]
            print(f"    {item_name} {piece_name}: {user_hex} (ΔE={de:.2f})")
    
    print(f"\nFound {len(dye_sets)} complete fade dye sets:")
    for dye_name, stage, pieces in dye_sets[:10]:
        max_de = max(p[2] for p in pieces)
        print(f"  {dye_name} Stage {stage} (worst ΔE={max_de:.2f})")
        for piece_idx, user_hex, de in pieces:
            piece_name = ['helmet', 'chestplate', 'leggings', 'boots'][piece_idx]
            print(f"    {piece_name}: {user_hex} (ΔE={de:.2f})")

    

