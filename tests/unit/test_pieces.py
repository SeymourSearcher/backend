"""The piece index is the wire format and the stored value.

The three name tables are index-aligned by position and the *_TO_PIECE maps are
derived from that position, so reordering any table would silently remap every
item_name already in the database. These tests pin the alignment.
"""
import pytest

from app.pieces import (
    ITEM_ID_TO_PIECE,
    ITEM_IDS,
    ITEM_NAME_TO_PIECE,
    ITEM_NAMES,
    PIECE_COUNT,
    Piece,
    SLOT_NAMES,
    SLOT_TO_PIECE,
)

TABLES = [SLOT_NAMES, ITEM_NAMES, ITEM_IDS]


@pytest.mark.parametrize("table", TABLES)
def test_every_table_covers_each_piece_exactly_once(table):
    assert len(table) == PIECE_COUNT
    assert len(set(table)) == PIECE_COUNT


def test_piece_count_matches_the_enum():
    assert PIECE_COUNT == len(Piece) == 4
    assert [p.value for p in Piece] == list(range(PIECE_COUNT))


@pytest.mark.parametrize("table, mapping", [
    (SLOT_NAMES, SLOT_TO_PIECE),
    (ITEM_NAMES, ITEM_NAME_TO_PIECE),
    (ITEM_IDS, ITEM_ID_TO_PIECE),
])
def test_lookup_maps_agree_with_table_position(table, mapping):
    assert mapping == {name: Piece(i) for i, name in enumerate(table)}
    for index, name in enumerate(table):
        assert mapping[name] == index


def test_stored_order_is_helmet_chestplate_leggings_boots():
    """Pinned: these indices are persisted, so the order is a data format."""
    assert (Piece.HELMET, Piece.CHESTPLATE, Piece.LEGGINGS, Piece.BOOTS) == (0, 1, 2, 3)
    assert SLOT_NAMES == ["helmet", "chestplate", "leggings", "boots"]
    assert ITEM_NAMES == [
        "Velvet Top Hat", "Cashmere Jacket", "Satin Trousers", "Oxford Shoes"
    ]
    assert ITEM_IDS == [
        "VELVET_TOP_HAT", "CASHMERE_JACKET", "SATIN_TROUSERS", "OXFORD_SHOES"
    ]


def test_piece_serialises_as_a_bare_int():
    """Piece rides through redis JSON and response models; it must not become a name."""
    import json

    assert json.dumps({"item_name": Piece.LEGGINGS}) == '{"item_name": 2}'
    assert json.loads(json.dumps(list(Piece))) == [0, 1, 2, 3]
