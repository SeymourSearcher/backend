"""Characterisation tests for find_custom_sets and its helpers.

Written before optimising it: at n=10,000 it takes 5.8s and dominates every
other function in compute.py combined, so it is the thing that will be rewritten.
These pin the current output exactly so a faster version has to agree with it.

The expected values were captured from the implementation as it stood, not
derived independently -- they assert "unchanged", not "correct".
"""
import numpy as np
import pytest

from app.services.compute import (
    KEY_ITEM_HEXES,
    _get_piece_labs,
    _expand,
    _greedy_select,
    _score_quads,
    find_custom_sets,
)

# Two tight sets (grey-ish and tan-ish) plus four unrelated decoys.
HAND_BUILT = [
    ("404040", 0), ("404141", 1), ("414040", 2), ("404041", 3),
    ("C08040", 0), ("C18141", 1), ("C08141", 2), ("C18040", 3),
    ("FF0000", 0), ("00FF00", 1), ("0000FF", 2), ("FFFFFF", 3),
]

HAND_BUILT_EXPECTED = [
    ("Custom Set #1", 0.3460255265235901, [
        (0, "C08040", 0.19035808742046356),
        (1, "C18141", 0.19000431895256042),
        (2, "C08141", 0.5018628835678101),
        (3, "C18040", 0.5018767714500427),
    ]),
    ("Custom Set #2", 0.42481327056884766, [
        (0, "404040", 0.20099951326847076),
        (1, "404141", 0.528888463973999),
        (2, "414040", 0.47407615184783936),
        (3, "404041", 0.49528902769088745),
    ]),
]


def make_inventory(n: int, seed: int = 0) -> list[tuple[str, int]]:
    """Jittered around real target hexes, so matches land the way real data does."""
    rng = np.random.default_rng(seed)
    targets = [h for h, _ in KEY_ITEM_HEXES]

    out = []
    for _ in range(n):
        base = int(targets[rng.integers(len(targets))], 16)
        r, g, b = (base >> 16) & 0xFF, (base >> 8) & 0xFF, base & 0xFF
        jitter = rng.integers(-6, 7, size=3)
        r, g, b = (int(np.clip(v + j, 0, 255)) for v, j in zip((r, g, b), jitter))
        out.append((f"{r:02X}{g:02X}{b:02X}", int(rng.integers(4))))
    return out


def assert_matches(actual, expected):
    assert len(actual) == len(expected)
    for (name, score, pieces), (exp_name, exp_score, exp_pieces) in zip(actual, expected):
        assert name == exp_name
        assert score == pytest.approx(exp_score, rel=1e-6)
        assert [(i, h) for i, h, _ in pieces] == [(i, h) for i, h, _ in exp_pieces]
        assert [de for _, _, de in pieces] == pytest.approx(
            [de for _, _, de in exp_pieces], rel=1e-6
        )


def test_hand_built_inventory_output_is_unchanged():
    assert_matches(find_custom_sets(HAND_BUILT, max_avg_delta_e=1.0), HAND_BUILT_EXPECTED)


def test_raising_the_threshold_keeps_already_qualifying_sets_identical():
    assert_matches(find_custom_sets(HAND_BUILT, max_avg_delta_e=3.0), HAND_BUILT_EXPECTED)


def test_seeded_inventory_output_is_unchanged():
    result = find_custom_sets(make_inventory(400, seed=7), max_avg_delta_e=1.0)

    assert_matches(result, [
        ("Custom Set #1", 0.15099263191223145, [
            (0, "01FF03", 0.06304223090410233),
            (1, "00FF00", 0.1842292845249176),
            (2, "01FF05", 0.21022845804691315),
            (3, "06FF01", 0.1464705467224121),
        ]),
    ])


def test_missing_piece_type_yields_nothing():
    assert find_custom_sets([("404040", 0), ("404141", 1), ("414040", 2)], 1.0) == []


def test_duplicate_hexes_do_not_produce_duplicate_sets():
    dupes = HAND_BUILT + [("404040", 0), ("404040", 0)]
    assert_matches(find_custom_sets(dupes, 1.0), HAND_BUILT_EXPECTED)


def test_output_is_deterministic():
    inventory = make_inventory(300, seed=3)
    assert find_custom_sets(inventory, 1.5) == find_custom_sets(inventory, 1.5)


def test_sets_never_reuse_a_hex_in_the_same_slot():
    """The disjointness _greedy_select exists to enforce."""
    result = find_custom_sets(make_inventory(1_500, seed=11), max_avg_delta_e=2.0)
    assert result

    for slot in range(4):
        hexes = [pieces[slot][1] for _, _, pieces in result]
        assert len(hexes) == len(set(hexes))


def test_scores_ascend_and_stay_under_the_threshold():
    result = find_custom_sets(make_inventory(1_500, seed=11), max_avg_delta_e=2.0)
    scores = [score for _, score, _ in result]

    assert scores == sorted(scores)
    assert all(s < 2.0 for s in scores)


def test_every_set_reports_all_four_piece_slots_in_order():
    for _, _, pieces in find_custom_sets(make_inventory(800, seed=5), max_avg_delta_e=2.0):
        assert [slot for slot, _, _ in pieces] == [0, 1, 2, 3]


def test_score_quads_values():
    """Same two sets the old per-triple scorer was pinned on, via index columns."""
    labs = [np.array([[50.0, 0.0, 0.0]], dtype=np.float32),
            np.array([[51.0, 0.0, 0.0]], dtype=np.float32),
            np.array([[52.0, 0.0, 0.0]], dtype=np.float32),
            np.array([[53.0, 0.0, 0.0], [80.0, 0.0, 0.0]], dtype=np.float32)]
    columns = [np.array([0, 0]), np.array([0, 0]), np.array([0, 0]), np.array([0, 1])]

    assert _score_quads(labs, columns) == pytest.approx([1.0, 10.875], rel=1e-6)


def test_expand_repeats_parents_and_flattens_neighbours():
    parents = np.array([10, 20, 30], dtype=np.int32)
    left, right = _expand([parents], np.array([[1, 2], [], [3]], dtype=object))

    assert left.tolist() == [10, 10, 30]
    assert right.tolist() == [1, 2, 3]


def greedy(rows, threshold):
    """rows are (score, i0, i1, i2, i3); returns the selected rows in order."""
    scores = np.array([r[0] for r in rows], dtype=np.float32)
    indices = np.array([r[1:] for r in rows], dtype=np.int32).reshape(len(rows), 4)
    return [rows[p] for p in _greedy_select(scores, indices, threshold)]


def test_greedy_select_takes_lowest_scores_first_and_keeps_slots_disjoint():
    candidates = [
        (0.5, 0, 0, 0, 0),
        (0.1, 1, 1, 1, 1),
        (0.2, 1, 2, 2, 2),  # reuses slot-0 index 1, so must be skipped
        (0.9, 3, 3, 3, 3),
        (5.0, 4, 4, 4, 4),  # over threshold
    ]
    assert greedy(candidates, 1.0) == [
        (0.1, 1, 1, 1, 1), (0.5, 0, 0, 0, 0), (0.9, 3, 3, 3, 3)
    ]


def test_greedy_select_breaks_ties_by_insertion_order():
    """Guards the sort's stability.

    np.argsort defaults to quicksort, which reorders equal scores and silently
    selects a different, equally scoring set.
    """
    ties = [(1.0, 0, 0, 0, 0), (1.0, 1, 1, 1, 1), (1.0, 0, 2, 2, 2)]
    assert greedy(ties, 5.0) == [(1.0, 0, 0, 0, 0), (1.0, 1, 1, 1, 1)]


def test_greedy_select_empty_input():
    empty = _greedy_select(
        np.empty(0, dtype=np.float32), np.empty((0, 4), dtype=np.int32), 1.0
    )
    assert len(empty) == 0


def test_get_piece_labs_requires_every_piece_type():
    assert _get_piece_labs([("404040", 0), ("404141", 1)]) == (None, None)

    pieces, labs = _get_piece_labs(HAND_BUILT)
    assert [len(p) for p in pieces] == [3, 3, 3, 3]
    assert all(lab.shape == (3, 3) for lab in labs)
