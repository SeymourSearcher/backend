import pytest

from app.services.matching import (
    TIER_THRESHOLDS,
    compute_abs_diff,
    get_name_for_hex,
    get_set_target_hexes,
    get_tier,
    match_columns,
    tier_bounds,
)

MATCH_KEYS = {
    "target_hex_keys", "delta_e_keys", "tier_keys",
    "target_hex_all", "delta_e_all", "tier_all",
}


@pytest.mark.parametrize("delta_e, expected", [
    (0.0, "t1"),
    (1.999, "t1"),
    (2.0, "t2"),
    (4.999, "t2"),
    (5.0, "t3"),
    (9.999, "t3"),
    (10.0, None),
    (999.0, None),
    (-0.1, None),
    (None, None),
])
def test_get_tier_boundaries(delta_e, expected):
    assert get_tier(delta_e) == expected


def test_tier_thresholds_are_contiguous_and_ordered():
    bounds = list(TIER_THRESHOLDS.values())
    assert bounds[0][0] == 0.0
    for (_, upper), (lower, _) in zip(bounds, bounds[1:]):
        assert upper == lower


def test_tier_bounds_unknown_tier_is_none():
    assert tier_bounds("t9") is None
    assert tier_bounds("") is None
    assert tier_bounds("t1") == (0.0, 2.0)


@pytest.mark.parametrize("a, b, expected", [
    ("000000", "000000", 0),
    ("FFFFFF", "000000", 765),
    ("010203", "030201", 4),
    (None, "FFFFFF", 0),
    ("FFFFFF", None, 0),
    ("", "FFFFFF", 0),
])
def test_compute_abs_diff(a, b, expected):
    assert compute_abs_diff(a, b) == expected


def test_compute_abs_diff_is_symmetric():
    assert compute_abs_diff("12AB34", "AB3412") == compute_abs_diff("AB3412", "12AB34")


def test_get_name_for_hex_handles_missing():
    assert get_name_for_hex(None) is None
    assert get_name_for_hex("") is None
    # A hex with no item or dye behind it resolves to nothing rather than raising.
    assert get_name_for_hex("123456") is None


def test_match_columns_empty_input():
    assert match_columns([]) == {}


def test_match_columns_shape_and_tier_consistency():
    columns = match_columns(["FF0000", "00FF00", "123456"])

    assert set(columns) == {"FF0000", "00FF00", "123456"}
    for values in columns.values():
        assert set(values) == MATCH_KEYS
        # tier is persisted separately from delta_e, so the two must agree or a
        # tier-filtered query silently disagrees with the delta_e it displays.
        assert values["tier_keys"] == get_tier(values["delta_e_keys"])
        assert values["tier_all"] == get_tier(values["delta_e_all"])


def test_match_columns_normalises_case_and_dedupes():
    columns = match_columns(["ff0000", "FF0000", "Ff0000"])
    assert list(columns) == ["FF0000"]


def test_match_columns_all_is_never_worse_than_keys():
    # `_all` searches key items plus dye stages, a superset, so its best match
    # can only be closer.
    for values in match_columns(["FF0000", "00FF00", "0000FF", "AABBCC"]).values():
        if values["delta_e_keys"] is not None and values["delta_e_all"] is not None:
            assert values["delta_e_all"] <= values["delta_e_keys"] + 1e-9


def test_get_set_target_hexes_unknown_set_is_empty():
    assert get_set_target_hexes("Not A Real Set") == []
