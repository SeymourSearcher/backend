"""Characterisation tests for the "sets" and "dyes" filter paths.

Written before swapping find_sets() -- which computes armor *and* dye sets -- for
whichever half each path actually uses. These pin the current output so the swap
has to agree with it.

The expected values were captured from the implementation as it stood, not
derived independently -- they assert "unchanged", not "correct".
"""
from types import SimpleNamespace

import pytest

from app.services.compute import find_armor_sets, find_fade_dye_sets, find_sets
from app.services.seymour_filters import filter_with_matches

# Real target hexes, so the set/dye matchers actually fire: a complete Farm Suit,
# a complete Aurora stage 1, half a Farm Armor, and a decoy.
INVENTORY = [
    ("FFFF00", 0), ("FFFF00", 1), ("FFFF00", 2), ("FFFF00", 3),
    ("048F95", 0), ("048F95", 1), ("048F95", 2), ("048F95", 3),
    ("FFD700", 0), ("FFD700", 1),
    ("123456", 2),
]

SEYMOURS = [
    SimpleNamespace(id=i, hex_color=h, item_name=p)
    for i, (h, p) in enumerate(INVENTORY)
]

FARM_SUIT_ROWS = [
    {"id": 0, "item_name": "Velvet Top Hat", "hex_color": "FFFF00",
     "match": {"target_name": "Farm Suit", "target_hex": "FFFF00", "delta_e": 0.0}},
    {"id": 1, "item_name": "Cashmere Jacket", "hex_color": "FFFF00",
     "match": {"target_name": "Farm Suit", "target_hex": "FFFF00", "delta_e": 0.0}},
    {"id": 2, "item_name": "Satin Trousers", "hex_color": "FFFF00",
     "match": {"target_name": "Farm Suit", "target_hex": "FFFF00", "delta_e": 0.0}},
    {"id": 3, "item_name": "Oxford Shoes", "hex_color": "FFFF00",
     "match": {"target_name": "Farm Suit", "target_hex": "FFFF00", "delta_e": 0.0}},
]


def target_names(rows):
    """Distinct match names, in the order the filter emitted them."""
    return list(dict.fromkeys(r["match"]["target_name"] for r in rows))


def test_find_sets_is_exactly_its_two_halves():
    """The identity the swap rests on: find_sets only pairs up the two matchers."""
    assert find_sets(INVENTORY) == (find_armor_sets(INVENTORY), find_fade_dye_sets(INVENTORY))


def test_sets_filter_output_is_unchanged():
    assert filter_with_matches(SEYMOURS, "sets") == FARM_SUIT_ROWS


def test_sets_filter_honours_sub_filter():
    assert filter_with_matches(SEYMOURS, "sets", sub_filter="Farm Suit") == FARM_SUIT_ROWS
    assert filter_with_matches(SEYMOURS, "sets", sub_filter="Nonexistent Set") == []


def test_dyes_filter_output_is_unchanged():
    rows = filter_with_matches(SEYMOURS, "dyes")

    assert len(rows) == 28
    assert target_names(rows) == [
        "Aurora Stage 1", "Aurora Stage 2", "Aurora Stage 71",
        "Warden Stage 14", "Warden Stage 15", "Warden Stage 16", "Warden Stage 17",
    ]
    assert rows[0] == {
        "id": 4, "item_name": "Velvet Top Hat", "hex_color": "048F95",
        "match": {"target_name": "Aurora Stage 1", "target_hex": "048F95", "delta_e": 0.0},
    }
    # Only the exact-match stage scores zero; the rest are near misses.
    assert [r["match"]["delta_e"] for r in rows[:8]] == [0.0] * 4 + [4.58] * 4


def test_dyes_filter_honours_sub_filter():
    rows = filter_with_matches(SEYMOURS, "dyes", sub_filter="Aurora")

    assert len(rows) == 12
    assert target_names(rows) == ["Aurora Stage 1", "Aurora Stage 2", "Aurora Stage 71"]
    assert filter_with_matches(SEYMOURS, "dyes", sub_filter="Nonexistent Dye") == []


def test_sets_incomplete_only_reports_owned_pieces_and_what_is_missing():
    rows = filter_with_matches(SEYMOURS, "sets", incomplete_only=True)

    assert target_names(rows) == ["Farm Armor", "Eleanor's Set"]
    assert rows[0] == {
        "id": 8, "item_name": "Velvet Top Hat", "hex_color": "FFD700",
        "match": {"target_name": "Farm Armor", "target_hex": "FFD700", "delta_e": 0.0,
                  "missing": ["Satin Trousers", "Oxford Shoes"]},
    }
    # The complete Farm Suit must not show up in an incomplete-only listing.
    assert all(r["hex_color"] == "FFD700" for r in rows)


def test_dyes_incomplete_only_reports_owned_pieces_and_what_is_missing():
    rows = filter_with_matches(SEYMOURS, "dyes", incomplete_only=True)

    assert rows
    assert all(r["hex_color"] == "FFD700" for r in rows)
    assert all(
        r["match"]["missing"] == ["Satin Trousers", "Oxford Shoes"] for r in rows
    )
    assert target_names(rows)[:3] == [
        "Sunflower Stage 6", "Sunflower Stage 7", "Sunflower Stage 8"
    ]


@pytest.mark.parametrize("filter_type", ["sets", "dyes"])
def test_empty_inventory_yields_nothing(filter_type):
    assert filter_with_matches([], filter_type) == []
