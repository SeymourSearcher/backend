"""The Rust kernel has to return exactly what the numpy search returns.

Differential rather than value-pinned on purpose. The `*_output_is_unchanged`
tests in test_compute_custom_sets.py hold absolute floats captured from a
--docker run, so they fail on a host interpreter for reasons that have nothing
to do with this port (see that file's docstring). Running both searches in one
process compares them against each other instead, which is the actual contract
and holds on any interpreter.

Equality is exact, not approximate: both paths do float32 arithmetic in the same
order, so any drift means the port diverged rather than that floats are floats.
"""
import numpy as np
import pytest

from app.services import compute
from app.services.compute import _get_piece_labs, _search_numpy, _search_rust

from tests.unit.test_compute_custom_sets import HAND_BUILT, make_inventory

pytestmark = pytest.mark.skipif(
    compute.seymour_sets is None, reason="rust/seymour_sets is not built"
)


def both(seymour_list, max_avg_delta_e):
    """(numpy, rust) results for one inventory, ordered the way the search sees it."""
    pieces, piece_labs = _get_piece_labs(seymour_list)
    if pieces is None:
        return [], []

    order = np.argsort([len(p) for p in pieces])
    piece_labs = [piece_labs[i] for i in order]
    radius = max_avg_delta_e * 2.0

    return (
        _search_numpy(piece_labs, radius, max_avg_delta_e),
        _search_rust(piece_labs, radius, max_avg_delta_e),
    )


def assert_identical(reference, ported):
    assert len(ported) == len(reference)
    for (want_score, want_idx), (got_score, got_idx) in zip(reference, ported):
        assert got_idx == want_idx
        assert float(got_score) == float(want_score)


def test_hand_built_inventory():
    assert_identical(*both(HAND_BUILT, 1.0))


def test_duplicate_hexes():
    assert_identical(*both(HAND_BUILT + [("404040", 0), ("404040", 0)], 1.0))


@pytest.mark.parametrize("seed", range(8))
def test_small_seeded_inventories(seed):
    assert_identical(*both(make_inventory(300, seed=seed), 1.0))


@pytest.mark.parametrize("n", [500, 1_500, 4_000])
def test_larger_seeded_inventories(n):
    assert_identical(*both(make_inventory(n, seed=n), 1.0))


@pytest.mark.parametrize("threshold", [0.25, 1.0, 1.3, 2.0, 3.0])
def test_thresholds_cut_in_the_same_place(threshold):
    """1.3 is deliberate: it has no exact float32 form, so an f32 comparison
    in the kernel would drop candidates numpy keeps."""
    assert_identical(*both(make_inventory(1_200, seed=11), threshold))


def test_wide_threshold_with_dense_ties():
    """A tight cluster makes equal scores common, which is what the stable sort
    and the lexicographic candidate order exist to resolve."""
    dense = [(f"4040{v:02X}", piece) for v in range(40) for piece in range(4)]
    assert_identical(*both(dense, 2.0))


def test_no_matches_within_radius():
    apart = [("000000", 0), ("FFFFFF", 1), ("00FF00", 2), ("FF00FF", 3)]
    reference, ported = both(apart, 0.5)
    assert reference == []
    assert ported == []


def test_find_custom_sets_uses_the_rust_path():
    """Guards the wiring: a broken import would silently fall back to numpy."""
    assert compute.find_custom_sets(HAND_BUILT, max_avg_delta_e=1.0)
    assert compute._search_numpy is not compute._search_rust
