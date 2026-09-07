import numpy as np
import pytest

from app.errors import InvalidInputError
from app.services.hex_pattern import PATTERN_LENGTH, matches, parse_pattern

# Small enough to walk exhaustively, so "which colours match" can be answered by
# brute force rather than trusted.
ALL_SIX_DIGIT = np.arange(0x1000000, dtype=np.uint32)


def naive(colour: int, raw: str) -> bool:
    """The grammar, transcribed one colour at a time."""
    digits = f"{colour:06X}"
    bindings: dict[str, str] = {}
    for digit, char in zip(digits, raw.upper()):
        if char in "0123456789ABCDEF":
            if digit != char:
                return False
        elif char != "?":
            if bindings.setdefault(char, digit) != digit:
                return False
    return True


def matching(raw: str, colours: np.ndarray) -> np.ndarray:
    return colours[matches(parse_pattern(raw), colours)]


@pytest.mark.parametrize("raw", ["", "12345", "1234567", "12345!", "12 456", "AB CDEF"])
def test_malformed_patterns_are_rejected(raw):
    with pytest.raises(InvalidInputError):
        parse_pattern(raw)


def test_a_leading_hash_is_stripped():
    assert matching("#FF00AA", np.array([0xFF00AA, 0xFF00AB], dtype=np.uint32)).tolist() == [
        0xFF00AA
    ]


def test_an_all_literal_pattern_matches_only_itself():
    pattern = parse_pattern("ABCDEF")

    assert (pattern.free, pattern.total) == (0, 1)
    assert matching("ABCDEF", ALL_SIX_DIGIT).tolist() == [0xABCDEF]


def test_literals_are_case_insensitive():
    assert parse_pattern("ff00aa") == parse_pattern("FF00AA")


def test_variables_are_case_insensitive():
    """x and X are the same variable, so mixed case must not split the binding."""
    assert parse_pattern("xXyyzz") == parse_pattern("XXYYZZ")


def test_distinct_variables_may_bind_to_the_same_digit():
    """No distinctness rule -- unlike marketplace.matches_hex_pattern."""
    found = matching("XXYYZZ", ALL_SIX_DIGIT)

    assert len(found) == 16 ** 3
    assert 0xAAAAAA in found


def test_wildcards_leave_the_literal_prefix_intact():
    pattern = parse_pattern("21????")
    found = matching("21????", ALL_SIX_DIGIT)

    assert (pattern.free, pattern.total) == (4, 65536)
    assert len(found) == 65536
    assert np.all(found >> 16 == 0x21)


def test_a_variable_repeat_binds_across_slots():
    pattern = parse_pattern("X2XYZZ")
    found = matching("X2XYZZ", ALL_SIX_DIGIT)

    assert (pattern.free, pattern.total) == (3, 4096)
    assert len(found) == 4096
    assert np.all((found >> 20) & 0xF == (found >> 12) & 0xF)  # slots 0 and 2
    assert np.all((found >> 16) & 0xF == 0x2)                  # slot 1 is literal
    assert np.all((found >> 4) & 0xF == found & 0xF)           # slots 4 and 5


@pytest.mark.parametrize(
    "raw", ["21????", "X2XYZZ", "XXYYZZ", "A?B?C?", "1X2Y3Z", "ABCDE?", "??00??", "X?X?X?"]
)
def test_the_mask_agrees_with_the_grammar_on_every_colour(raw):
    """Exhaustive against a naive transcription over a slice wide enough to hit
    every slot, plus the exact count over the whole space."""
    sample = np.arange(0x000000, 0x010000, dtype=np.uint32)
    expected = [c for c in sample.tolist() if naive(c, raw)]

    assert matching(raw, sample).tolist() == expected
    assert len(matching(raw, ALL_SIX_DIGIT)) == parse_pattern(raw).total


@pytest.mark.parametrize("raw", ["??????", "XYZWVU", "?????X", "xyzwvu"])
def test_an_unconstrained_pattern_matches_everything(raw):
    """A variable used once constrains nothing, so all of these span the space."""
    pattern = parse_pattern(raw)
    sample = np.array([0x000000, 0x123456, 0xFFFFFF], dtype=np.uint32)

    assert pattern.free == PATTERN_LENGTH
    assert pattern.total == 0x1000000
    assert matching(raw, sample).tolist() == sample.tolist()


def test_an_empty_candidate_set_is_handled():
    assert matching("21????", np.empty(0, dtype=np.uint32)).tolist() == []
