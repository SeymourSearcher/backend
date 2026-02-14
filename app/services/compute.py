import numpy as np
import json

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
find sets for the users hex codes

involving others:
query to find pieces to buy from others to get
duplicates
make a complete set

'''

def all_hex_codes():
    return list(range(0x1000000))


if __name__ == "__main__":
    with open('backend/app/static/data.json', 'r') as f:
        data = json.load(f)
    pieces = {
        'helmet': ,
        'chestplate': [],
        'leggings': [],
        'boots': []
    }
    hexes = [hex for category in data['categories'].values() for hex in category]
    print(len(hexes))
    print(hexes[:10])
