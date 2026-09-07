"""Ownership measured against the searched colour itself, not via the catalog.

Distances from FFFF00, to save recomputing them while reading:
    FFFF00 0.00   FFFF05 0.34   FEFF00 0.43   FFFE00 0.57
    FFFA00 2.87   F0FF00 6.36   000000 137.21
"""
import numpy as np

from app.services.compute import ALL_LAB, get_lab
from app.services.owner_hexes import _build
from app.services.owner_matches import count_owners_within, find_owner_matches

SEARCH = "FFFF00"


def owners(*rows):
    """rows are (user_id, username, hex_color, piece)."""
    return _build([
        (uid, name, uid + 100, None, hex_color, piece)
        for uid, name, hex_color, piece in rows
    ])


def distance(hex_color: str) -> float:
    return float(np.linalg.norm(get_lab(hex_color) - get_lab(SEARCH)))


def test_only_holdings_inside_the_radius_come_back():
    table = owners(
        (1, "near", "FFFE00", 0),
        (2, "far", "FFFA00", 0),
        (3, "opposite", "000000", 0),
    )

    assert [m["username"] for m in find_owner_matches(table, SEARCH)] == ["near"]


def test_the_radius_excludes_its_own_boundary():
    table = owners((1, "onedge", "FFFE00", 0))

    assert find_owner_matches(table, SEARCH, max_delta_e=distance("FFFE00")) == []


def test_identical_holdings_collapse_into_a_quantity():
    table = owners(
        (1, "hoarder", "FFFE00", 1),
        (1, "hoarder", "FFFE00", 1),
        (1, "hoarder", "FFFE00", 1),
    )

    matches = find_owner_matches(table, SEARCH)

    assert len(matches) == 1
    assert matches[0]["quantity"] == 3


def test_the_same_colour_on_a_different_piece_is_a_separate_holding():
    table = owners(
        (1, "hoarder", "FFFE00", 1),
        (1, "hoarder", "FFFE00", 2),
    )

    matches = find_owner_matches(table, SEARCH)

    assert [(m["piece"], m["quantity"]) for m in matches] == [(1, 1), (2, 1)]


def test_closest_first_then_username():
    table = owners(
        (1, "zoe", "FFFF05", 0),
        (2, "adam", "FFFF05", 0),
        (3, "closest", "FFFF00", 0),
        (4, "furthest", "FFFE00", 0),
    )

    assert [m["username"] for m in find_owner_matches(table, SEARCH)] == [
        "closest", "adam", "zoe", "furthest",
    ]


def test_a_match_carries_its_own_colour_not_the_searched_one():
    table = owners((1, "near", "FFFE00", 3))

    match = find_owner_matches(table, SEARCH)[0]

    assert match["hex_color"] == "FFFE00"
    assert match["discord_id"] == "101"
    assert match["delta_e"] == round(distance("FFFE00"), 2)


def test_the_count_is_distinct_users_not_holdings():
    table = owners(
        (1, "hoarder", "FFFE00", 0),
        (1, "hoarder", "FEFF00", 1),
        (1, "hoarder", "FFFF05", 2),
        (2, "other", "FFFF00", 0),
        (3, "far", "F0FF00", 0),
    )

    assert count_owners_within(table, SEARCH) == 2


def test_an_empty_table_yields_nothing():
    table = owners()

    assert find_owner_matches(table, SEARCH) == []
    assert count_owners_within(table, SEARCH) == 0


def test_the_lab_table_covers_the_whole_colour_space():
    """find_owner_matches fancy-indexes it with raw hex ints and never bounds-checks."""
    assert len(ALL_LAB) == 0x1000000
