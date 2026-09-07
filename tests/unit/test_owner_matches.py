"""Ownership measured against the searched colour itself, not via the catalog.

Distances from FFFF00, to save recomputing them while reading:
    FFFF00 0.00   FFFF05 0.34   FEFF00 0.43   FFFE00 0.57
    FFFA00 2.87   F0FF00 6.36   FFE800 13.32   000000 137.21
"""
import numpy as np

from app.services.compute import ALL_LAB, get_lab
from app.services.owner_hexes import _build
from app.services.owner_matches import find_owner_matches, tier_users

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


def test_an_empty_table_yields_nothing():
    table = owners()

    assert find_owner_matches(table, SEARCH) == []
    assert tier_users(table, SEARCH) == {"t1": [], "t2": [], "t3": []}


class TestTiers:
    """FFFA00 is 2.87 away (t2), F0FF00 is 6.36 (t3), FFE800 is 13.32 (nowhere)."""

    def test_users_land_in_the_tier_their_distance_earns(self):
        table = owners(
            (1, "onthenose", "FFFF00", 0),
            (2, "middling", "FFFA00", 0),
            (3, "distant", "F0FF00", 0),
            (4, "offscale", "FFE800", 0),
        )

        tiers = tier_users(table, SEARCH)

        assert [u["username"] for u in tiers["t1"]] == ["onthenose"]
        assert [u["username"] for u in tiers["t2"]] == ["middling"]
        assert [u["username"] for u in tiers["t3"]] == ["distant"]

    def test_a_user_appears_once_at_their_closest_holding(self):
        """Owning a t3 piece as well as a t1 one must not list them twice."""
        table = owners(
            (1, "collector", "FFFF00", 0),
            (1, "collector", "F0FF00", 1),
        )

        tiers = tier_users(table, SEARCH)

        assert [u["username"] for u in tiers["t1"]] == ["collector"]
        assert tiers["t2"] == [] and tiers["t3"] == []
        assert tiers["t1"][0]["delta_e"] == 0.0

    def test_each_tier_is_closest_first(self):
        table = owners(
            (1, "further", "FFFE00", 0),
            (2, "nearer", "FFFF05", 0),
        )

        assert [u["username"] for u in tier_users(table, SEARCH)["t1"]] == [
            "nearer", "further",
        ]

    def test_the_t1_bucket_matches_the_owners_list_radius(self):
        """T1 is ΔE 0-2, so its members are exactly the users find_owner_matches
        surfaces -- the page shows one count, not two that disagree."""
        table = owners(
            (1, "inside", "FFFE00", 0),
            (2, "outside", "FFFA00", 0),
        )

        t1 = {u["username"] for u in tier_users(table, SEARCH)["t1"]}
        listed = {m["username"] for m in find_owner_matches(table, SEARCH)}

        assert t1 == listed == {"inside"}


def test_the_lab_table_covers_the_whole_colour_space():
    """find_owner_matches fancy-indexes it with raw hex ints and never bounds-checks."""
    assert len(ALL_LAB) == 0x1000000
