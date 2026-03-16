"""
Precompute hex neighborhoods for each target hex.
For each target, stores all hex codes within delta E threshold.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
from app.services.compute import (
    ALL_LAB, UNIQUE_KEY_HEXES, UNIQUE_ALL_HEXES,
    KEY_ITEM_TREE, ALL_ITEM_TREE, _hex_list_to_ints
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'app', 'static')
THRESHOLD = 10.0
BATCH_SIZE = 500_000


def precompute_neighborhoods(hex_list: list[str], tree, output_name: str):
    total = 0x1000000
    target_labs = ALL_LAB[_hex_list_to_ints(hex_list)]
    
    neighbors = {h: [] for h in hex_list}
    hex_to_idx = {h: i for i, h in enumerate(hex_list)}
    
    print(f"Precomputing neighborhoods for {len(hex_list)} targets...")
    
    for start in range(0, total, BATCH_SIZE):
        end = min(start + BATCH_SIZE, total)
        batch_labs = ALL_LAB[start:end]
        
        dists, indices = tree.query(batch_labs, k=1)
        
        for i, (dist, target_idx) in enumerate(zip(dists, indices)):
            if dist < THRESHOLD:
                hex_int = start + i
                target_hex = hex_list[target_idx]
                neighbors[target_hex].append(hex_int)
        
        pct = (end / total) * 100
        print(f"  {pct:.1f}% ({end:,} / {total:,})")
    
    neighborhoods = {h: np.array(v, dtype=np.uint32) for h, v in neighbors.items()}
    
    output_path = os.path.join(STATIC_DIR, output_name)
    np.save(output_path, neighborhoods, allow_pickle=True)
    
    total_neighbors = sum(len(v) for v in neighborhoods.values())
    print(f"Saved: {output_path} ({os.path.getsize(output_path) / 1024 / 1024:.1f} MB)")
    print(f"Total neighbor entries: {total_neighbors:,}")
    print(f"Avg neighbors per target: {total_neighbors / len(hex_list):.0f}")


def main():
    precompute_neighborhoods(UNIQUE_KEY_HEXES, KEY_ITEM_TREE, 'hex_neighborhoods_keys.npy')
    precompute_neighborhoods(UNIQUE_ALL_HEXES, ALL_ITEM_TREE, 'hex_neighborhoods_all.npy')
    print("Done!")


if __name__ == "__main__":
    main()
