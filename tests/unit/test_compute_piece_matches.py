"""find_top_hex_matches with a slot filter.

Per-piece trees rather than post-filtering the global tree: a slot with few
items would need an unbounded k before enough of its colours survived.
"""
import pytest

from app.pieces import PIECE_COUNT, Piece
from app.services.compute import HEX_TO_PIECES, find_top_hex_matches

SEARCH = "A3F2C1"


@pytest.mark.parametrize("piece", list(Piece))
def test_every_match_is_available_on_the_requested_slot(piece):
    matches = find_top_hex_matches(SEARCH, 3, False, [piece])

    assert matches
    for target_hex, _ in matches:
        assert piece in HEX_TO_PIECES[target_hex]


def test_results_stay_closest_first():
    des = [de for _, de in find_top_hex_matches(SEARCH, 3, False, [Piece.HELMET])]

    assert des == sorted(des)


def test_the_limit_is_honoured():
    assert len(find_top_hex_matches(SEARCH, 2, False, [Piece.HELMET])) == 2


def test_asking_for_every_slot_matches_the_unfiltered_query():
    every = find_top_hex_matches(SEARCH, 3, False, list(range(PIECE_COUNT)))
    unfiltered = find_top_hex_matches(SEARCH, 3, False)

    assert [h for h, _ in every] == [h for h, _ in unfiltered]


def test_a_union_of_slots_is_at_least_as_close_as_either_alone():
    """Merging two trees must not push a nearer colour out of the top result."""
    helmet = find_top_hex_matches(SEARCH, 1, False, [Piece.HELMET])[0][1]
    boots = find_top_hex_matches(SEARCH, 1, False, [Piece.BOOTS])[0][1]
    both = find_top_hex_matches(SEARCH, 1, False, [Piece.HELMET, Piece.BOOTS])[0][1]

    assert both == pytest.approx(min(helmet, boots))


def test_an_out_of_range_slot_is_ignored_rather_than_crashing():
    """parse_pieces deliberately lets a bad index through so it matches nothing."""
    assert find_top_hex_matches(SEARCH, 3, False, [99]) == []


def test_dyes_widen_the_candidates_for_a_slot():
    keys = find_top_hex_matches(SEARCH, 1, False, [Piece.HELMET])[0][1]
    with_dyes = find_top_hex_matches(SEARCH, 1, True, [Piece.HELMET])[0][1]

    assert with_dyes <= keys
