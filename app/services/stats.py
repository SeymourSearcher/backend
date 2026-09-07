"""Turning one user's Seymour list into their nine tallied stats.

Pure computation -- persistence and the ranking read path live in
app/services/rankings.py, and anything needing a database (pricing) builds on
categorise_collection from app/services/collection_value.py.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.services.compute import find_matching_seymours, find_sets
from app.services.compute import KEY_ITEM_HEXES, HEX_WORDS
from app.services.compute import seymour_converter
from app.services.matching import get_tier


def count_dupes(seymour_list):
    counts = Counter(seymour_list)
    return sum(c - 1 for c in counts.values() if c > 1)

HEX_SEQUENCE = "0123456789ABCDEF"
HEX_SEQUENCE_REV = HEX_SEQUENCE[::-1]

def _is_sequential(h):
    return h in HEX_SEQUENCE or h in HEX_SEQUENCE_REV

def _is_repeating(h):
    return h[0:2] == h[2:4] == h[4:6]

def _is_aabbcc(h):
    return h[0] == h[1] and h[2] == h[3] and h[4] == h[5]

def _is_triple_pattern(h):
    return h[0] == h[2] == h[4] and h[1] == h[3] == h[5]

def _is_palindrome(h):
    return h == h[::-1]

def _contains_word(h):
    for word in HEX_WORDS:
        if word in h:
            return True
    return False

def is_star(hex_code):
    h = hex_code.upper()
    return _is_sequential(h) or _is_repeating(h) or _is_aabbcc(h) or _is_triple_pattern(h)

def count_stars(seymour_list):
    return sum(1 for hex_code, _ in seymour_list if is_star(hex_code))

def count_words(seymour_list):
    return sum(1 for hex_code, _ in seymour_list if _contains_word(hex_code.upper()))

def get_tier_data(matches):
    counts = Counter(get_tier(match[2]) for match in matches)
    return counts["t1"], counts["t2"], counts["t3"]

@dataclass(frozen=True)
class ItemRow:
    """One entry of a collection, tagged with every category /items slices by.

    One row per entry rather than per distinct colour, duplicates included: the
    tier tallies have always counted items, because find_matching_seymours
    groups by piece without deduping.
    """
    hex_color: str
    piece: int
    target_hex: str | None
    delta_e: float | None
    tier: str | None
    star: bool
    word: bool
    # Set on every copy after the first, so summing these reproduces
    # count_dupes -- which counts excess copies, not items involved in a dupe.
    excess_dupe: bool
    # Set on the first copy only. A set consumes one copy, so tagging every copy
    # would let a valuation price armour the user cannot assemble twice.
    in_armor_set: bool
    in_dye_set: bool


@dataclass(frozen=True)
class CollectionBreakdown:
    rows: list[ItemRow]
    # Counts of complete *sets*, not of the items in them -- the unit the
    # Sets/Dyes chips have always displayed.
    armor_sets: int
    dye_sets: int


def categorise_collection(seymour_list) -> CollectionBreakdown:
    """Every category in two KD-tree passes, tagged per item.

    Asking filter_with_matches once per category would repeat the tree walk nine
    times for the same answers; both passes here are shared by all of them.

    The private pool is load-bearing, not incidental: this runs inside an
    asyncio.to_thread (see app/services/rankings.py), so submitting to the
    default executor instead -- which is what to_thread would do -- puts a
    thread of that pool in a blocking wait on the same pool. That deadlocks at
    cpu_count+4 concurrent uploads.

    Linear in inventory size. Re-measure with scripts/bench_compute.py.
    """
    with ThreadPoolExecutor(max_workers=2) as executor:
        matches_future = executor.submit(
            find_matching_seymours, seymour_list, KEY_ITEM_HEXES, threshold=10.0, best_only=True
        )
        sets_future = executor.submit(find_sets, seymour_list)

        matches = matches_future.result()
        armor_sets, dye_sets = sets_future.result()

    best = {user: (target[0], de) for user, target, de in matches}
    armor_keys = {(hex_color, piece) for _, owned in armor_sets for _, piece, hex_color, _ in owned}
    dye_keys = {(hex_color, piece) for _, _, members in dye_sets for piece, hex_color, _ in members}

    seen = Counter()
    rows = []
    for hex_color, piece in seymour_list:
        key = (hex_color, piece)
        seen[key] += 1
        first = seen[key] == 1
        target_hex, delta_e = best.get(key, (None, None))

        rows.append(ItemRow(
            hex_color=hex_color,
            piece=piece,
            target_hex=target_hex,
            delta_e=delta_e,
            tier=None if delta_e is None else get_tier(delta_e),
            star=is_star(hex_color),
            word=_contains_word(hex_color.upper()),
            excess_dupe=not first,
            in_armor_set=first and key in armor_keys,
            in_dye_set=first and key in dye_keys,
        ))

    return CollectionBreakdown(rows=rows, armor_sets=len(armor_sets), dye_sets=len(dye_sets))


def get_stats(seymour_list):
    """The nine tallies, reduced from one categorisation pass.

    Reduced rather than counted separately so a chip's count and its valuation
    can never disagree about which items they describe.
    """
    breakdown = categorise_collection(seymour_list)
    rows = breakdown.rows
    tiers = Counter(row.tier for row in rows)

    return {
        "stars": sum(1 for row in rows if row.star),
        "t1": tiers["t1"],
        "t2": tiers["t2"],
        "t3": tiers["t3"],
        "words": sum(1 for row in rows if row.word),
        "dupes": sum(1 for row in rows if row.excess_dupe),
        "total_sets": breakdown.armor_sets,
        "dyed_sets": breakdown.dye_sets,
        "total_seymours": len(rows),
    }
