"""The six-slot hex pattern grammar, as a mask over colours.

    0-9 A-F a-f   literal digit
    ?             free wildcard
    G-Z g-z       bound variable; every slot holding it holds the same digit

Upper-casing before parsing gives both case rules at once: `a` becomes the
literal A, `x` becomes the same variable as X.

Deliberately not the grammar of marketplace.matches_hex_pattern, which has no
wildcard and additionally requires distinct variables to bind to distinct
digits. That one decides which live buy orders are fillable, so it cannot be
loosened to match this. TODO: fold the two once buy orders can be migrated.

Tested against the colours in the database rather than used to enumerate the
16.7M space: the owned set is four orders of magnitude smaller, so filtering it
beats generating a pattern's members and intersecting, and it needs no special
case for a pattern that constrains nothing.
"""
from dataclasses import dataclass

import numpy as np

from app.errors import InvalidInputError

PATTERN_LENGTH = 6
WILDCARD = "?"
_LITERALS = "0123456789ABCDEF"


@dataclass(frozen=True)
class HexPattern:
    """`base` holds the literal digits, already shifted into place, and
    `literal_mask` marks which nibbles they occupy. `var_groups` lists the shifts
    of each variable used more than once -- a variable used once constrains
    nothing and is dropped at parse time, which is why `?????X` spans the whole
    space exactly as `??????` does."""

    raw: str
    base: int
    literal_mask: int
    var_groups: tuple[tuple[int, ...], ...]
    free: int

    @property
    def total(self) -> int:
        return 16 ** self.free


def _shift(slot: int) -> int:
    """Nibble shift of a 0-based slot, counting from the left."""
    return 4 * (PATTERN_LENGTH - 1 - slot)


def parse_pattern(raw: str) -> HexPattern:
    normalised = raw.strip().lstrip("#").upper()
    if len(normalised) != PATTERN_LENGTH:
        raise InvalidInputError(f"Pattern must be {PATTERN_LENGTH} characters")

    base = 0
    literal_mask = 0
    by_variable: dict[str, list[int]] = {}
    free = 0

    for slot, char in enumerate(normalised):
        shift = _shift(slot)
        if char in _LITERALS:
            base += _LITERALS.index(char) << shift
            literal_mask |= 0xF << shift
        elif char == WILDCARD:
            free += 1
        elif "G" <= char <= "Z":
            if char not in by_variable:
                by_variable[char] = []
                free += 1
            by_variable[char].append(shift)
        else:
            raise InvalidInputError(f"Invalid pattern character {char!r}")

    groups = tuple(tuple(shifts) for shifts in by_variable.values() if len(shifts) > 1)
    return HexPattern(normalised, base, literal_mask, groups, free)


def matches(pattern: HexPattern, colours: np.ndarray) -> np.ndarray:
    """Boolean mask over `colours` (uint32 0xRRGGBB) of the ones that match."""
    colours = np.asarray(colours, dtype=np.uint32)
    ok = np.ones(len(colours), dtype=bool)

    if pattern.literal_mask:
        ok &= (colours & np.uint32(pattern.literal_mask)) == np.uint32(pattern.base)

    for shifts in pattern.var_groups:
        first = (colours >> np.uint32(shifts[0])) & np.uint32(0xF)
        for shift in shifts[1:]:
            ok &= first == ((colours >> np.uint32(shift)) & np.uint32(0xF))

    return ok


def like_mask(pattern: HexPattern) -> str:
    """The literal slots kept, everything else `_`, as a SQL LIKE mask.

    Safe to interpolate: parse_pattern admits no character LIKE treats
    specially. It expresses only the literals -- variables need `variable_slots`
    on top of it.
    """
    return "".join(
        _LITERALS[(pattern.base >> _shift(slot)) & 0xF]
        if (pattern.literal_mask >> _shift(slot)) & 0xF
        else "_"
        for slot in range(PATTERN_LENGTH)
    )


def variable_slots(pattern: HexPattern) -> tuple[tuple[int, ...], ...]:
    """Each repeated variable's slots, 1-based from the left for SQL SUBSTRING."""
    return tuple(
        tuple(PATTERN_LENGTH - shift // 4 for shift in shifts)
        for shifts in pattern.var_groups
    )
