import numpy as np
import pytest

from app.errors import InvalidInputError
from app.services.hex_pattern import PATTERN_LENGTH, colour_indices, parse_pattern


def indices(raw: str) -> np.ndarray:
    return colour_indices(parse_pattern(raw))


def matches(colour: int, raw: str) -> bool:
    """Naive reference: the definition, one colour at a time."""
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


@pytest.mark.parametrize("raw", ["", "12345", "1234567", "12345!", "12 456", "AB CDEF"])
def test_malformed_patterns_are_rejected(raw):
    with pytest.raises(InvalidInputError):
        parse_pattern(raw)


def test_a_leading_hash_is_stripped():
    assert indices("#FF00AA").tolist() == [0xFF00AA]


def test_an_all_literal_pattern_denotes_one_colour():
    pattern = parse_pattern("ABCDEF")

    assert pattern.free == 0
    assert pattern.total == 1
    assert colour_indices(pattern).tolist() == [0xABCDEF]


def test_literals_are_case_insensitive():
    assert parse_pattern("ff00aa") == parse_pattern("FF00AA")


def test_variables_are_case_insensitive():
    """x and X are the same variable, so mixed case must not split the binding."""
    assert np.array_equal(indices("xXyyzz"), indices("XXYYZZ"))


def test_distinct_variables_may_bind_to_the_same_digit():
    """No distinctness rule -- unlike marketplace.matches_hex_pattern."""
    result = indices("XXYYZZ")

    assert len(result) == 16 ** 3
    assert 0xAAAAAA in result


def test_wildcards_leave_the_literal_prefix_intact():
    pattern = parse_pattern("21????")

    assert (pattern.free, pattern.total) == (4, 65536)
    assert np.all(colour_indices(pattern) >> 16 == 0x21)


def test_a_variable_repeat_binds_across_slots():
    pattern = parse_pattern("X2XYZZ")
    result = colour_indices(pattern)

    assert (pattern.free, pattern.total) == (3, 4096)
    assert np.all((result >> 20) & 0xF == (result >> 12) & 0xF)  # slots 0 and 2
    assert np.all((result >> 16) & 0xF == 0x2)                   # slot 1 is literal
    assert np.all((result >> 4) & 0xF == result & 0xF)           # slots 4 and 5


@pytest.mark.parametrize("raw", ["21????", "X2XYZZ", "XXYYZZ", "A?B?C?", "ABCDE?", "??00??"])
def test_generated_colours_ascend(raw):
    """Every consumer slices, intersects or stable-sorts these; none re-sorts."""
    assert np.all(np.diff(indices(raw)) > 0)


@pytest.mark.parametrize("raw", ["21????", "X2XYZZ", "XXYYZZ", "A?B?C?", "1X2Y3Z"])
def test_generated_colours_are_exactly_the_matching_ones(raw):
    """Every generated colour matches, and the count equals 16**free -- together
    those pin set equality without walking all 16.7M."""
    pattern = parse_pattern(raw)
    result = colour_indices(pattern)

    assert len(result) == pattern.total
    assert all(matches(int(c), raw) for c in result)


@pytest.mark.parametrize("raw", ["??????", "XYZWVU", "?????X", "xyzwvu"])
def test_an_unconstrained_pattern_refuses_to_generate(raw):
    """A variable used once is a wildcard, so all of these span the whole space
    and the caller has to take the ordered-index branch instead."""
    pattern = parse_pattern(raw)

    assert pattern.free == PATTERN_LENGTH
    assert pattern.total == 0x1000000
    with pytest.raises(ValueError):
        colour_indices(pattern)
