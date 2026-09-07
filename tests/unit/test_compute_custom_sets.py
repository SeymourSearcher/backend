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
    """Jittered around real target hexes, so matches land the way real data does.

    Seeded off KEY_ITEM_HEXES, so a catalog sync reshuffles it -- re-pin the
    seeded expectations from a --docker run, not the host interpreter.
    """
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
        ("Custom Set #1", 0.780685305595398, [
            (0, "000003", 0.3470931351184845),
            (1, "010305", 1.0258095264434814),
            (2, "030003", 0.7226572632789612),
            (3, "010006", 1.0271812677383423),
        ]),
        ("Custom Set #2", 0.9833168983459473, [
            (0, "25F5E7", 1.483636498451233),
            (1, "23F3E7", 0.47035670280456543),
            (2, "2FF0E4", 1.1541125774383545),
            (3, "2CF2E7", 0.8251618146896362),
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
    (left, right), counts = _expand([parents], np.array([[1, 2], [], [3]], dtype=object))

    assert left.tolist() == [10, 10, 30]
    assert right.tolist() == [1, 2, 3]
    assert counts.tolist() == [2, 0, 1]


def greedy(rows, threshold, counts=None):
    """rows are (score, i0, i1, i2, i3), grouped into triples by `counts`."""
    scores = np.array([r[0] for r in rows], dtype=np.float32)
    indices = np.array([r[1:] for r in rows], dtype=np.int32).reshape(len(rows), 4)
    if counts is None:
        counts = np.ones(len(rows), dtype=np.intp)
    return [rows[p] for p in _greedy_select(scores, indices, np.asarray(counts), threshold)]


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


def test_greedy_select_falls_back_within_a_triple_when_its_best_slot3_is_taken():
    """The refill path. Dropping a triple on its first collision loses set #2."""
    rows = [
        (0.1, 0, 0, 0, 0),   # triple A, wins and consumes slot-3 piece 0
        (0.2, 1, 1, 1, 0),   # triple B's best, now blocked on slot-3 piece 0
        (0.3, 1, 1, 1, 1),   # triple B's fallback, still free
    ]
    assert greedy(rows, 1.0, counts=[1, 2]) == [(0.1, 0, 0, 0, 0), (0.3, 1, 1, 1, 1)]


def test_greedy_select_drops_a_triple_once_its_fixed_pieces_are_used():
    """No refill can help: slots 0-2 are consumed for good by the first emission."""
    rows = [
        (0.1, 0, 0, 0, 0),
        (0.2, 0, 0, 0, 1),   # same triple as the winner -- dead, not refilled
    ]
    assert greedy(rows, 1.0, counts=[2]) == [(0.1, 0, 0, 0, 0)]


def reference_greedy(scores, indices, threshold):
    """The obvious implementation: sort every candidate, take the first still-free one.

    _greedy_select replaced this with a per-triple heap for speed; it has to agree.
    """
    order = np.argsort(scores, kind="stable")
    used = [set(), set(), set(), set()]
    selected = []
    for position in order[scores[order] < threshold].tolist():
        row = indices[position].tolist()
        if any(row[slot] in used[slot] for slot in range(4)):
            continue
        for slot in range(4):
            used[slot].add(row[slot])
        selected.append(position)
    return selected


@pytest.mark.parametrize("seed", range(25))
def test_greedy_select_agrees_with_sorting_every_candidate(seed):
    """Random triples with random fan-out, including collisions and dead triples."""
    rng = np.random.default_rng(seed)
    counts = rng.integers(0, 6, size=60)

    triples = rng.integers(0, 6, size=(len(counts), 3)).astype(np.int32)
    indices = np.concatenate([
        np.column_stack([
            np.repeat(triples, counts, axis=0),
            rng.integers(0, 8, size=int(counts.sum())).astype(np.int32),
        ])
    ]).reshape(-1, 4)
    # Coarse, so equal scores are common and tie-breaking actually gets exercised.
    scores = (rng.integers(1, 12, size=len(indices)) / 10).astype(np.float32)

    assert _greedy_select(scores, indices, counts, 1.0).tolist() == reference_greedy(
        scores, indices, 1.0
    )


def test_greedy_select_empty_input():
    empty = _greedy_select(
        np.empty(0, dtype=np.float32),
        np.empty((0, 4), dtype=np.int32),
        np.empty(0, dtype=np.intp),
        1.0,
    )
    assert len(empty) == 0


def test_get_piece_labs_requires_every_piece_type():
    assert _get_piece_labs([("404040", 0), ("404141", 1)]) == (None, None)

    pieces, labs = _get_piece_labs(HAND_BUILT)
    assert [len(p) for p in pieces] == [3, 3, 3, 3]
    assert all(lab.shape == (3, 3) for lab in labs)
