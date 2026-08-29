"""The 16.7M colour indices ordered by distance to their nearest target.

The unscoped visualisation ("All Hexes") asks which colours sit within a delta E
band of the nearest catalog item. That answer depends on the band and
include_dyes and nothing else -- not on the caller, not on the database -- so
the KD query it used to run per request (16.7M points, ~1.1s, ~200MB of
transient allocation to produce three integers) is replaced by a lookup into a
precomputed permutation.

Ordering by distance makes a band a contiguous slice, which is what collapses
the cost: a count is a subtraction, and a sample needs only the bounds. The
distance itself is not stored -- the bounds are found by binary search that
recomputes one point per probe, 24 single-point queries at ~17us against a
67MB-per-variant array. See scripts/precompute_hex_order.py.
"""
import os
from dataclasses import dataclass

import numpy as np

from app.services.compute import (
    ALL_ITEM_TREE,
    ALL_LAB,
    KEY_ITEM_TREE,
    STATIC_DIR,
)


@dataclass(frozen=True)
class ColorSelection:
    """The colours matching a request, as `source[lo:hi]`.

    Both the banded and the per-user paths land in this one shape: the band is a
    slice of the ordered index, and a per-user query is the whole of its own
    filtered array. Nothing is materialised until `sample` is called, so
    /visualization/stats never builds an array at all.
    """

    source: np.ndarray
    lo: int
    hi: int
    total: int

    @property
    def count(self) -> int:
        return self.hi - self.lo

    def sample(self, max_points: int, rng: np.random.Generator) -> np.ndarray:
        if self.count <= max_points:
            return np.asarray(self.source[self.lo:self.hi])
        return np.asarray(self.source[self.lo + rng.choice(self.count, max_points, replace=False)])

    @classmethod
    def of(cls, indices: np.ndarray, total: int) -> "ColorSelection":
        return cls(indices, 0, len(indices), total)


def _load_order(name: str) -> np.ndarray | None:
    """Memory-mapped so the 67MB stays in page cache rather than resident."""
    path = os.path.join(STATIC_DIR, f"hex_order_{name}.npy")
    return np.load(path, mmap_mode="r") if os.path.exists(path) else None


ORDER_KEYS = _load_order("keys")
ORDER_ALL = _load_order("all")

# Callers fall back to querying the tree directly when the artifact has not been
# generated, matching how app.services.compute treats the hex_matches files.
HAS_ORDER = ORDER_KEYS is not None and ORDER_ALL is not None


def _order_and_tree(include_dyes: bool):
    if include_dyes:
        return ORDER_ALL, ALL_ITEM_TREE
    return ORDER_KEYS, KEY_ITEM_TREE


def _boundary(order: np.ndarray, tree, delta_e: float) -> int:
    """First position in `order` whose distance is >= delta_e.

    Matches np.searchsorted(..., side="left") on the distances, without storing
    them. The probe must use the same tree the ordering was built from or the
    search is not monotonic.
    """
    if delta_e <= 0.0:
        return 0

    low, high = 0, len(order)
    while low < high:
        mid = (low + high) // 2
        if float(tree.query(ALL_LAB[order[mid]], k=1)[0]) < delta_e:
            low = mid + 1
        else:
            high = mid
    return low


def band(min_de: float, max_de: float, include_dyes: bool) -> ColorSelection:
    """Every RGB colour whose nearest target lies in [min_de, max_de)."""
    order, tree = _order_and_tree(include_dyes)
    low = _boundary(order, tree, min_de)
    return ColorSelection(order, low, max(low, _boundary(order, tree, max_de)), len(order))
