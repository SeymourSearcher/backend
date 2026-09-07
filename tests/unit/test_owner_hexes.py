"""The snapshot is packed binary inside JSON, so a round-trip through the cache
has to come back byte-identical or the matching pass silently scores wrong users.
"""
import json

from app.services.owner_hexes import OwnerHexes, _build, _decode, _encode

ROWS = [
    (7, "exact", 11, None, "FFFF00", 0),
    (7, "exact", 11, None, "000000", 3),
    (9, "close", 12, "abc", "FFFE00", 1),
]


def round_trip(rows) -> OwnerHexes:
    """Through json, exactly as CacheEntry.set/get would."""
    return _decode(json.loads(json.dumps(_encode(_build(rows)))))


def test_each_user_is_stored_once_and_rows_point_at_them():
    owners = _build(ROWS)

    assert owners.users == [[7, "exact", 11, None], [9, "close", 12, "abc"]]
    assert list(owners.idx) == [0, 0, 1]


def test_hex_colours_are_stored_as_ints():
    assert list(_build(ROWS).hexes) == [0xFFFF00, 0x000000, 0xFFFE00]


def test_pieces_stay_aligned_with_their_rows():
    assert list(_build(ROWS).pieces) == [0, 3, 1]


def test_the_round_trip_preserves_everything():
    original, restored = _build(ROWS), round_trip(ROWS)

    assert restored.users == original.users
    assert list(restored.idx) == list(original.idx)
    assert list(restored.hexes) == list(original.hexes)
    assert list(restored.pieces) == list(original.pieces)


def test_an_empty_table_round_trips():
    owners = round_trip([])

    assert owners.users == []
    assert list(owners.idx) == []
    assert list(owners.hexes) == []
    assert list(owners.pieces) == []


def test_lowercase_hex_survives_as_the_same_colour():
    """The column is not case-normalised, but the int and the rebuilt string are."""
    owners = round_trip([(7, "exact", 11, None, "ffff00", 0)])

    assert list(owners.hexes) == [0xFFFF00]
    assert f"{owners.hexes[0]:06X}" == "FFFF00"
