import json

import pytest

from app.pieces import Piece
from app.services.parser import parse_seymour_file

PIPE = "Velvet Top Hat | #{hex} | Top: Aurora - Stage 20 (ΔE: 5.00 | Abs: 55)"
QUOTED = '"uuid" "Satin Trousers" "{hex}" "Fig Armor 3p 2.1"'
MARKDOWN = "`CASHMERE_JACKET`, hex: `{hex}` (**ORIGINAL**) - Location: chest"


@pytest.mark.parametrize("template, piece", [
    (PIPE, Piece.HELMET),
    (QUOTED, Piece.LEGGINGS),
    (MARKDOWN, Piece.CHESTPLATE),
])
def test_each_format_parses(template, piece):
    parsed, skipped = parse_seymour_file(template.format(hex="15E9E8"))

    assert skipped == 0
    assert [(s.item_name, s.hex_color) for s in parsed] == [(piece, "15E9E8")]


@pytest.mark.parametrize("template", [PIPE, QUOTED, MARKDOWN])
@pytest.mark.parametrize("bad_hex", [
    "garbage",
    "ZZZZZZ",
    "15E9E",
    "15E9E8A",
    "",
])
def test_malformed_hex_is_skipped_not_stored(template, bad_hex):
    parsed, skipped = parse_seymour_file(template.format(hex=bad_hex))

    assert parsed == []
    assert skipped == 1


@pytest.mark.parametrize("template", [PIPE, QUOTED, MARKDOWN])
def test_hex_is_normalised_to_upper_case(template):
    parsed, _ = parse_seymour_file(template.format(hex="abcdef"))

    assert parsed[0].hex_color == "ABCDEF"


def test_unknown_item_name_is_skipped():
    parsed, skipped = parse_seymour_file("Bogus Item | #15E9E8 | x")

    assert parsed == []
    assert skipped == 1


def test_one_bad_line_does_not_discard_the_good_ones():
    content = "\n".join([
        PIPE.format(hex="15E9E8"),
        PIPE.format(hex="garbage"),
        QUOTED.format(hex="0A9256"),
    ])

    parsed, skipped = parse_seymour_file(content)

    assert [s.hex_color for s in parsed] == ["15E9E8", "0A9256"]
    assert skipped == 1


def test_json_format_counts_bad_hex_as_skipped():
    content = json.dumps({
        "u1": {"pieceName": "Velvet Top Hat", "hexcode": "#15E9E8"},
        "u2": {"pieceName": "Cashmere Jacket", "hexcode": "nothex"},
        "u3": {"pieceName": "Satin Trousers", "hexcode": "abcdef"},
        "u4": {"pieceName": "Bogus Item", "hexcode": "FFFFFF"},
    })

    parsed, skipped = parse_seymour_file(content)

    assert [(s.item_name, s.hex_color) for s in parsed] == [
        (Piece.HELMET, "15E9E8"),
        (Piece.LEGGINGS, "ABCDEF"),
    ]
    assert skipped == 2


def test_blank_lines_are_not_counted_as_skipped():
    content = f"\n\n{PIPE.format(hex='15E9E8')}\n\n   \n"

    parsed, skipped = parse_seymour_file(content)

    assert len(parsed) == 1
    assert skipped == 0
