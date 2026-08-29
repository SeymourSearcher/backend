"""
Precompute the delta-E ordering of all 16.7M hex codes.

Emits one uint32 permutation per target set -- the colour indices sorted by
distance to their nearest target -- which is what turns a delta E band into a
contiguous slice at request time. See app/services/color_index.py.

Run after anything that changes the catalog (app/static/data.json), alongside
scripts/precompute_hex_matches.py. Also rerun after a scipy upgrade: the
ordering is only exact against the distances the installed cKDTree produces.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
from scipy.spatial import cKDTree

from app.services.compute import ALL_ITEM_TREE, ALL_LAB, KEY_ITEM_TREE, STATIC_DIR

BATCH_SIZE = 500_000


def _distances(tree: cKDTree) -> np.ndarray:
    """Nearest-target distance for every RGB colour, batched to bound peak memory.

    Kept at the float64 cKDTree returns. Rounding to float32 here would collapse
    near-ties that color_index._boundary still sees apart when it re-queries the
    tree, leaving the ordering unsorted with respect to that probe -- a band edge
    then lands anywhere within the collapsed run.
    """
    total = len(ALL_LAB)
    out = np.empty(total, dtype=np.float64)

    for start in range(0, total, BATCH_SIZE):
        end = min(start + BATCH_SIZE, total)
        out[start:end], _ = tree.query(ALL_LAB[start:end], k=1, workers=-1)
        print(f"  {end:,} / {total:,}", end="\r", flush=True)

    print()
    return out


def precompute_order(tree: cKDTree, name: str) -> None:
    print(f"Ordering {name}...")
    distances = _distances(tree)

    # Stable so that regenerating from an unchanged catalog reproduces the file
    # byte for byte, and so ties resolve by colour index rather than arbitrarily.
    order = np.argsort(distances, kind="stable").astype(np.uint32)

    path = os.path.join(STATIC_DIR, f"hex_order_{name}.npy")
    np.save(path, order)
    print(f"  wrote {path} ({order.nbytes / 1e6:.0f} MB)")


if __name__ == "__main__":
    precompute_order(KEY_ITEM_TREE, "keys")
    precompute_order(ALL_ITEM_TREE, "all")
