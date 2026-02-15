import numpy as np
import json
import time
import os

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

def find_matching_seymours(user_seymours, threshold=10.0):
    """
    Finds user seymours within delta E threshold of target seymours (same piece type).
    Returns list of (user_seymour, target_seymour, delta_e_value).
    Scipy spatial distance cdist could be faster?
    """
    user_by_piece = _group_by_piece(user_seymours)
    target_by_piece = _group_by_piece(KEY_ITEM_HEXES)
    
    threshold_sq = threshold * threshold
    results = []
    
    for piece in range(4):
        user_hexes = user_by_piece.get(piece)
        target_hexes = target_by_piece.get(piece)
        if not user_hexes or not target_hexes:
            continue
        
        user_labs = ALL_LAB[_hex_list_to_ints(user_hexes)]
        target_labs = ALL_LAB[_hex_list_to_ints(target_hexes)]
        
        diff = user_labs[:, np.newaxis, :] - target_labs[np.newaxis, :, :]
        dist_sq = np.einsum('ijk,ijk->ij', diff, diff)
        
        ui, ti = np.where(dist_sq < threshold_sq)
        distances = np.sqrt(dist_sq[ui, ti])
        
        results.extend(
            ((user_hexes[u], piece), (target_hexes[t], piece), float(d))
            for u, t, d in zip(ui, ti, distances)
        )
    
    return results

def get_key_item_hexes():
    with open(os.path.join(os.path.dirname(__file__), '..', 'static', 'data.json'), 'r') as f:
        data = json.load(f)
    pieces = {
        'helmet': 0,
        'chestplate': 1,
        'leggings': 2,
        'boots': 3,
    }
    key_item_hexes = []
    for category in data['categories'].values():
        for item in category:
            for piece in pieces.values():
                key_item_hexes.append((item['hex'], piece))
    return key_item_hexes
KEY_ITEM_HEXES = get_key_item_hexes()

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
    
    matches = find_matching_seymours(seymour_list, threshold=5.0)
    print(f"\nFound {len(matches)} matches with delta E < 5")
    for user_s, target_s, de in matches[:10]:
        print(f"  {user_s} -> {target_s} (ΔE={de:.2f})")

    

