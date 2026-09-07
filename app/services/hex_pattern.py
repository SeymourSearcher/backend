"""The six-slot hex pattern grammar, and the colour set a pattern denotes.

    0-9 A-F a-f   literal digit
    ?             free wildcard
    G-Z g-z       bound variable; every slot holding it holds the same digit

Upper-casing before parsing gives both case rules at once: `a` becomes the
literal A, `x` becomes the same variable as X.

Deliberately not the grammar of marketplace.matches_hex_pattern, which has no
wildcard and additionally requires distinct variables to bind to distinct
digits. That one decides which live buy orders are fillable, so it cannot be
loosened to match this. TODO: fold the two once buy orders can be migrated.

The set is generated rather than filtered -- a predicate over 16.7M colours is
not affordable, and the free slots already describe the answer exactly.
"""
from dataclasses import dataclass

import numpy as np

from app.errors import InvalidInputError

PATTERN_LENGTH = 6
WILDCARD = "?"
_LITERALS = "0123456789ABCDEF"


@dataclass(frozen=True)
class HexPattern:
    """`base` holds the literal digits, already shifted into place.

    Each free group -- a wildcard slot, or all the slots sharing one variable --
    contributes one weight, the sum of the position values it occupies. A colour
    matches iff it is `base` plus some digit times each weight.
    """

    raw: str
    base: int
    weights: tuple[int, ...]

    @property
    def free(self) -> int:
        return len(self.weights)

    @property
    def total(self) -> int:
        return 16 ** self.free


def parse_pattern(raw: str) -> HexPattern:
    normalised = raw.strip().lstrip("#").upper()
    if len(normalised) != PATTERN_LENGTH:
        raise InvalidInputError(f"Pattern must be {PATTERN_LENGTH} characters")

    base = 0
    groups: list[int] = []
    by_variable: dict[str, int] = {}

    for slot, char in enumerate(normalised):
        value = 1 << (4 * (PATTERN_LENGTH - 1 - slot))
        if char in _LITERALS:
            base += _LITERALS.index(char) * value
        elif char == WILDCARD:
            groups.append(value)
        elif "G" <= char <= "Z":
            seen = by_variable.get(char)
            if seen is None:
                by_variable[char] = len(groups)
                groups.append(value)
            else:
                groups[seen] += value
        else:
            raise InvalidInputError(f"Invalid pattern character {char!r}")

    # Groups are already in order of their most significant slot, which is what
    # makes colour_indices ascending; sorting descending only restates it.
    return HexPattern(normalised, base, tuple(sorted(groups, reverse=True)))


def colour_indices(pattern: HexPattern) -> np.ndarray:
    """Every colour the pattern matches, ascending.

    Ascending is load-bearing -- intersect1d, searchsorted and the hex tiebreak
    (a stable sort keyed on ΔE alone) all assume it. It holds because a group
    owning slot i has weight >= 16**(5-i), while every later group owns only
    slots after i and so sums to at most 16**(5-i) - 1: one step of the earlier
    group always outruns the later ones combined.
    """
    if pattern.free == PATTERN_LENGTH:
        raise ValueError("Unconstrained pattern; the caller must slice the ordered index")

    n = pattern.total
    codes = np.arange(n, dtype=np.uint32)
    out = np.full(n, pattern.base, dtype=np.uint32)
    for k, weight in enumerate(pattern.weights):
        out += ((codes >> (4 * (pattern.free - 1 - k))) & 0xF) * np.uint32(weight)
    return out
