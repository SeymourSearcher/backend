"""
Precompute best hex matches for all 16.7M possible hex codes.
Generates .npy files for O(1) lookups at runtime.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
from scipy.spatial import cKDTree

from app.services.compute import (
    ALL_LAB, UNIQUE_KEY_HEXES, UNIQUE_ALL_HEXES,
    KEY_ITEM_TREE, ALL_ITEM_TREE, _hex_list_to_ints
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'app', 'static')
BATCH_SIZE = 500_000


def build_hex_to_idx_map(hex_list: list[str]) -> dict[str, int]:
    return {h: i for i, h in enumerate(hex_list)}


def precompute_matches(tree: cKDTree, hex_list: list[str], output_prefix: str):
    total = 0x1000000
    hex_to_idx = build_hex_to_idx_map(hex_list)
    
    indices = np.empty(total, dtype=np.uint16)
    delta_es = np.empty(total, dtype=np.float16)
    
    print(f"Precomputing matches for {output_prefix} ({len(hex_list)} targets)...")
    
    for start in range(0, total, BATCH_SIZE):
        end = min(start + BATCH_SIZE, total)
        batch_labs = ALL_LAB[start:end]
        
        dists, tree_indices = tree.query(batch_labs, k=1)
        
        indices[start:end] = tree_indices.astype(np.uint16)
        delta_es[start:end] = dists.astype(np.float16)
        
        pct = (end / total) * 100
        print(f"  {pct:.1f}% ({end:,} / {total:,})")
    
    idx_path = os.path.join(STATIC_DIR, f'{output_prefix}.npy')
    de_path = os.path.join(STATIC_DIR, f'{output_prefix}_de.npy')
    
    np.save(idx_path, indices)
    np.save(de_path, delta_es)
    
    print(f"Saved: {idx_path} ({os.path.getsize(idx_path) / 1024 / 1024:.1f} MB)")
    print(f"Saved: {de_path} ({os.path.getsize(de_path) / 1024 / 1024:.1f} MB)")


def main():
    precompute_matches(KEY_ITEM_TREE, UNIQUE_KEY_HEXES, 'hex_matches_keys')
    precompute_matches(ALL_ITEM_TREE, UNIQUE_ALL_HEXES, 'hex_matches_all')
    print("Done!")


if __name__ == "__main__":
    main()
