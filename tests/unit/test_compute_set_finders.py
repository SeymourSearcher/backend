"""Characterisation tests for the four set finders.

Written before collapsing the demand-counting logic that find_armor_sets,
find_incomplete_armor_sets, find_fade_dye_sets and find_incomplete_dye_sets each
re-implement. These pin the current output so the shared version has to agree.

The expected values were captured from the implementation as it stood, not
derived independently -- they assert "unchanged", not "correct".
"""
from app.pieces import Piece
from app.services.compute import (
    find_armor_sets,
    find_fade_dye_sets,
    find_incomplete_armor_sets,
    find_incomplete_dye_sets,
)

# Real target hexes, so the matchers actually fire: a complete Farm Suit, a
# complete Aurora stage 1, half a Farm Armor, and a decoy.
INVENTORY = [
    ("FFFF00", 0), ("FFFF00", 1), ("FFFF00", 2), ("FFFF00", 3),
    ("048F95", 0), ("048F95", 1), ("048F95", 2), ("048F95", 3),
    ("FFD700", 0), ("FFD700", 1),
    ("123456", 2),
]

# Reaper Armour 3p is the only set whose two items collide on a piece slot, and
# their hexes sit 282 apart -- so reaching the duplicate branch needs a hex
# between them and a threshold far above the 5.0 production default.
DUPLICATE_INVENTORY = [("801010", 1), ("801010", 2), ("801010", 3)]
DUPLICATE_THRESHOLD = 60.0


def test_find_armor_sets_output_is_unchanged():
    assert find_armor_sets(INVENTORY) == [
        ("Farm Suit", [
            ("Farm Suit", Piece.HELMET, "FFFF00", 0.0),
            ("Farm Suit", Piece.CHESTPLATE, "FFFF00", 0.0),
            ("Farm Suit", Piece.LEGGINGS, "FFFF00", 0.0),
            ("Farm Suit", Piece.BOOTS, "FFFF00", 0.0),
        ]),
    ]


def test_find_fade_dye_sets_output_is_unchanged():
    result = find_fade_dye_sets(INVENTORY)
    assert [(name, stage) for name, stage, _ in result] == [
        ("Aurora", 1), ("Aurora", 2), ("Aurora", 71),
        ("Warden", 14), ("Warden", 15), ("Warden", 16), ("Warden", 17),
    ]
    assert result[0] == ("Aurora", 1, [
        (0, "048F95", 0.0), (1, "048F95", 0.0),
        (2, "048F95", 0.0), (3, "048F95", 0.0),
    ])


def test_find_incomplete_armor_sets_output_is_unchanged():
    result = find_incomplete_armor_sets(INVENTORY)
    assert [name for name, _, _ in result] == ["Farm Armor", "Eleanor's Set"]
    assert result[0] == ("Farm Armor", [
        ("Farm Armor", Piece.HELMET, "FFD700", 0.0),
        ("Farm Armor", Piece.CHESTPLATE, "FFD700", 0.0),
    ], [
        ("Farm Armor", Piece.LEGGINGS),
        ("Farm Armor", Piece.BOOTS),
    ])


def test_find_incomplete_dye_sets_output_is_unchanged():
    result = find_incomplete_dye_sets(INVENTORY)
    assert len(result) == 15
    assert [(name, stage) for name, stage, _, _ in result][:3] == [
        ("Sunflower", 6), ("Sunflower", 7), ("Sunflower", 8),
    ]
    assert result[0] == ("Sunflower", 6, [
        (0, "FFD700", 3.8596670627593994),
        (1, "FFD700", 3.8596670627593994),
    ], [2, 3])


def test_incomplete_armor_sets_flag_a_piece_two_items_both_need():
    """The duplicate branch: one owned hex is the best match for both Reaper items."""
    result = [
        entry
        for entry in find_incomplete_armor_sets(DUPLICATE_INVENTORY, threshold=DUPLICATE_THRESHOLD)
        if entry[0].startswith("Reaper")
    ]
    assert len(result) == 1
    set_name, owned, missing = result[0]
    assert set_name == "Reaper Armour 3p"
    assert len(owned) == 6
    assert missing == [("duplicate_needed", -1)]


def test_complete_armor_sets_reject_a_piece_two_items_both_need():
    """Same inventory, complete finder: the shortfall drops the set entirely."""
    result = [
        entry
        for entry in find_armor_sets(DUPLICATE_INVENTORY, threshold=DUPLICATE_THRESHOLD)
        if entry[0].startswith("Reaper")
    ]
    assert result == []


def test_dye_sets_never_demand_a_piece_twice():
    """A dye stage asks for each slot exactly once, so no owned piece is ever
    double-booked. This is why the dye finders' duplicate branch stays dormant."""
    for _, _, _, missing in find_incomplete_dye_sets(INVENTORY):
        assert -1 not in missing
