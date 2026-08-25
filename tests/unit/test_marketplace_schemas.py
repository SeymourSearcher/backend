"""hex_value reaches int(v, 16) for every match type except PATTERN, which binds
non-hex characters as variables instead. The schema is the only thing keeping a
crafted order from raising for every user matched against it.
"""
import pytest
from pydantic import ValidationError

from app.models.marketplace import HexMatchType
from app.pieces import Piece
from app.schemas.marketplace import HexCriteriaCreate, SetCriteriaCreate
from app.services.marketplace import matches_delta_e, matches_hex_pattern


def hex_criteria(match_type, hex_value):
    return HexCriteriaCreate(match_type=match_type, hex_value=hex_value, max_delta_e=5.0)


@pytest.mark.parametrize("match_type", [HexMatchType.EXACT, HexMatchType.DELTA_E])
@pytest.mark.parametrize("bad_hex", ["ZZZZZZ", "GGGGGG", "12345!", "      "])
def test_non_hex_rejected_for_numeric_match_types(match_type, bad_hex):
    with pytest.raises(ValidationError):
        hex_criteria(match_type, bad_hex)


@pytest.mark.parametrize("pattern", ["XXYYZZ", "AAXXBB", "AABBCC"])
def test_pattern_accepts_non_hex_variables(pattern):
    assert hex_criteria(HexMatchType.PATTERN, pattern).hex_value == pattern


@pytest.mark.parametrize("match_type", list(HexMatchType))
@pytest.mark.parametrize("bad_length", ["", "15E9E", "15E9E8A"])
def test_wrong_length_rejected_for_every_match_type(match_type, bad_length):
    with pytest.raises(ValidationError):
        hex_criteria(match_type, bad_length)


@pytest.mark.parametrize("match_type", list(HexMatchType))
def test_hex_value_is_upper_cased(match_type):
    assert hex_criteria(match_type, "15e9e8").hex_value == "15E9E8"


def test_rejected_values_are_exactly_the_ones_that_would_raise_downstream():
    with pytest.raises(ValueError):
        matches_delta_e("15E9E8", "ZZZZZZ", 5.0)

    # The same input is inert under PATTERN, which is why the check is conditional.
    assert matches_hex_pattern("15E9E8", "ZZZZZZ") is False


def test_set_criteria_rejects_empty_pieces_and_dedupes():
    with pytest.raises(ValidationError):
        SetCriteriaCreate(set_name="Farm Suit", piece_types=[], tier="t1")

    criteria = SetCriteriaCreate(
        set_name="Farm Suit", piece_types=[Piece.BOOTS, Piece.HELMET, Piece.BOOTS], tier="t1"
    )
    assert criteria.piece_types == [Piece.HELMET, Piece.BOOTS]
