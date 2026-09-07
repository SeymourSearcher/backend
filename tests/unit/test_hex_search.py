"""The pattern search, exercised without a DB.

_search takes the owner snapshot directly, so every case here is pure: build a
table with owner_hexes._build, ask for a page, assert on the rows. Results are
always colours somebody holds, so a table with no rows has no answers.
"""
import numpy as np
import pytest

from app.services import compute
from app.services.hex_pattern import parse_pattern
from app.services.hex_search import _page, _search
from app.services.matching import get_tier
from app.services.owner_hexes import _build

ME = 1
SOMEONE_ELSE = 2


def owners(*rows):
    """rows are (user_id, username, hex_color, piece)."""
    return _build([
        (uid, name, uid + 100, None, hex_color, piece)
        for uid, name, hex_color, piece in rows
    ])


def search(table, raw, **kwargs):
    kwargs.setdefault("include_dyes", False)
    kwargs.setdefault("tier", None)
    kwargs.setdefault("pieces", None)
    kwargs.setdefault("viewer_id", None)
    kwargs.setdefault("mine_only", False)
    kwargs.setdefault("limit", 10)
    kwargs.setdefault("offset", 0)
    return _search(table, parse_pattern(raw), **kwargs)


def codes(result):
    return [row["hex_code"] for row in result["items"]]


class TestScope:
    def test_only_colours_somebody_holds_come_back(self):
        table = owners(
            (1, "holder", "210000", 0),
            (2, "other", "21FFFF", 1),
            (3, "elsewhere", "990000", 0),
        )

        result = search(table, "21????")

        assert result["total"] == 2
        assert set(codes(result)) == {"210000", "21FFFF"}

    def test_pattern_total_still_reports_the_whole_space(self):
        """The gap between the two is what says how rare the pattern is."""
        table = owners((1, "holder", "210000", 0))

        result = search(table, "21????")

        assert (result["total"], result["pattern_total"]) == (1, 16 ** 4)

    def test_an_empty_table_has_no_answers(self):
        result = search(owners(), "21????")

        assert result["total"] == 0 and result["items"] == []

    def test_a_pattern_nobody_matches_is_empty_rather_than_padded(self):
        table = owners((1, "holder", "990000", 0))

        assert search(table, "21????")["total"] == 0

    def test_duplicate_holdings_collapse_to_one_row(self):
        table = owners(
            (1, "a", "210000", 0),
            (2, "b", "210000", 1),
        )

        result = search(table, "21????")

        assert result["total"] == 1
        assert result["items"][0]["owner_count"] == 2


class TestMineOnly:
    def test_it_narrows_to_the_callers_holdings(self):
        table = owners(
            (ME, "me", "210000", 0),
            (SOMEONE_ELSE, "them", "21FFFF", 0),
        )

        assert codes(search(table, "21????", viewer_id=ME, mine_only=True)) == ["210000"]

    def test_without_it_everyones_holdings_are_in_scope(self):
        table = owners(
            (ME, "me", "210000", 0),
            (SOMEONE_ELSE, "them", "21FFFF", 0),
        )

        assert set(codes(search(table, "21????", viewer_id=ME))) == {"210000", "21FFFF"}

    def test_an_unknown_viewer_owns_nothing_rather_than_everything(self):
        table = owners((SOMEONE_ELSE, "them", "210000", 0))

        assert search(table, "21????", viewer_id=999, mine_only=True)["total"] == 0


class TestOwnedByMe:
    def test_it_marks_the_callers_own_colours(self):
        table = owners(
            (ME, "me", "210000", 0),
            (SOMEONE_ELSE, "them", "21FFFF", 0),
        )

        by_code = {r["hex_code"]: r["owned_by_me"] for r in search(
            table, "21????", viewer_id=ME
        )["items"]}

        assert by_code == {"210000": True, "21FFFF": False}

    def test_a_colour_shared_with_someone_else_is_still_mine(self):
        table = owners(
            (ME, "me", "210000", 0),
            (SOMEONE_ELSE, "them", "210000", 1),
        )

        row = search(table, "21????", viewer_id=ME)["items"][0]

        assert row["owned_by_me"] is True and row["owner_count"] == 2

    def test_an_anonymous_caller_owns_nothing(self):
        table = owners((ME, "me", "210000", 0))

        assert search(table, "21????")["items"][0]["owned_by_me"] is False

    def test_with_mine_only_every_row_is_mine(self):
        table = owners(
            (ME, "me", "210000", 0),
            (SOMEONE_ELSE, "them", "21FFFF", 0),
        )

        rows = search(table, "21????", viewer_id=ME, mine_only=True)["items"]

        assert all(row["owned_by_me"] for row in rows)


class TestOwners:
    def test_every_row_names_who_holds_it(self):
        table = owners((1, "holder", "210000", 0))

        row = search(table, "21????")["items"][0]

        assert [o["username"] for o in row["owners"]] == ["holder"]
        assert row["owners"][0]["pieces"] == [0]

    def test_a_user_collapses_across_pieces(self):
        table = owners(
            (1, "collector", "210000", 0),
            (1, "collector", "210000", 3),
        )

        row = search(table, "21????")["items"][0]

        assert row["owner_count"] == 1
        assert row["owners"][0]["pieces"] == [0, 3] and row["owners"][0]["quantity"] == 2


class TestOrdering:
    def test_rows_ascend_by_delta_e(self):
        table = owners(
            (1, "a", "21DDE5", 0),
            (2, "b", "210000", 0),
            (3, "c", "21D4E4", 0),
        )

        des = [row["delta_e"] for row in search(table, "21????")["items"]]

        assert des == sorted(des)

    def test_ties_break_on_the_colour_itself(self):
        """Candidates ascend and the sort is stable, so equal ΔE keeps hex order.

        Driven directly because genuine float16 ties are rare in real data, and
        rounding two distinct ΔE to 2dp only looks like one."""
        colours = np.array([0x10, 0x20, 0x30, 0x40], dtype=np.uint32)
        de = np.array([5.0, 1.0, 1.0, 1.0], dtype=np.float32)

        assert _page(colours, de, limit=3, offset=0).tolist() == [0x20, 0x30, 0x40]

    def test_the_tie_window_does_not_lose_rows_at_the_cut(self):
        """argpartition picks an arbitrary member of a tied group, so the cut
        value has to be widened to all of them before sorting."""
        colours = np.array([0x10, 0x20, 0x30], dtype=np.uint32)
        de = np.array([2.0, 2.0, 2.0], dtype=np.float32)

        assert _page(colours, de, limit=1, offset=1).tolist() == [0x20]

    def test_paging_does_not_repeat_or_skip(self):
        table = owners(*[(i, f"u{i}", f"21{i:04X}", 0) for i in range(1, 7)])

        both = codes(search(table, "21????", limit=6))
        first = codes(search(table, "21????", limit=3, offset=0))
        second = codes(search(table, "21????", limit=3, offset=3))

        assert first + second == both


class TestTier:
    def test_the_tier_shown_is_the_one_the_delta_e_earns(self):
        """Pinned to matching.get_tier rather than to a copy of the bands."""
        table = owners(*[(i, f"u{i}", f"21{i:04X}", 0) for i in range(1, 12)])

        for row in search(table, "21????")["items"]:
            assert row["tier"] == get_tier(row["delta_e"])

    def test_a_tier_selects_exactly_the_rows_that_earn_it(self):
        table = owners(*[(i, f"u{i}", f"21{i:04X}", 0) for i in range(1, 12)])
        unfiltered = search(table, "21????", limit=50)["items"]
        expected = {r["hex_code"] for r in unfiltered if r["tier"] == "t1"}

        result = search(table, "21????", tier="t1", limit=50)

        assert result["total"] == len(expected)
        assert set(codes(result)) == expected

    def test_all_is_not_a_filter(self):
        table = owners((1, "a", "210000", 0))

        assert search(table, "21????", tier="all")["total"] == search(table, "21????")["total"]


class TestPieceFilter:
    """The piece filter is ownership-side only; ΔE and the catalog match are
    always global. These are the tests that stop it reaching the KD path."""

    def test_it_drops_colours_held_only_on_other_slots(self):
        table = owners(
            (1, "helmetonly", "210000", 0),
            (2, "bootsonly", "21FFFF", 3),
        )

        assert codes(search(table, "21????", pieces=[3])) == ["21FFFF"]

    def test_a_surviving_colour_keeps_its_global_delta_e(self):
        table = owners(
            (1, "helmetonly", "210000", 0),
            (2, "bootsonly", "210000", 3),
        )

        plain = search(table, "21????")["items"][0]
        filtered = search(table, "21????", pieces=[3])["items"][0]

        assert filtered["delta_e"] == plain["delta_e"]
        assert filtered["target_name"] == plain["target_name"]
        assert [o["username"] for o in filtered["owners"]] == ["bootsonly"]

    def test_an_out_of_range_piece_matches_nothing_rather_than_widening(self):
        table = owners((1, "holder", "210000", 0))

        assert search(table, "21????", pieces=[9])["total"] == 0

    def test_it_composes_with_mine_only(self):
        table = owners(
            (ME, "me", "210000", 0),
            (ME, "me", "21FFFF", 3),
        )

        result = search(table, "21????", viewer_id=ME, mine_only=True, pieces=[3])

        assert codes(result) == ["21FFFF"]


class TestUnconstrained:
    """??????, XYZWVU and ?????X all span the whole space, so they return the
    holdings themselves -- which is the cheapest case, not the most expensive."""

    @pytest.mark.parametrize("raw", ["??????", "XYZWVU", "?????X"])
    def test_it_returns_every_held_colour(self, raw):
        table = owners(
            (1, "a", "210000", 0),
            (2, "b", "990000", 0),
        )

        result = search(table, raw)

        assert result["pattern_total"] == 0x1000000
        assert result["total"] == 2

    def test_with_mine_only_it_is_the_callers_whole_collection(self):
        table = owners(
            (ME, "me", "210000", 0),
            (SOMEONE_ELSE, "them", "990000", 0),
        )

        assert codes(search(table, "??????", viewer_id=ME, mine_only=True)) == ["210000"]


def test_the_kd_fallback_agrees_with_the_precomputed_tables(monkeypatch):
    """float16 vs float64 diverge in the last place, so compare at display
    precision -- the same 2dp the response rounds to."""
    if not compute.USE_PRECOMPUTED:
        pytest.skip("no precomputed tables to compare against")

    table = owners(*[(i, f"u{i}", f"21{i:04X}", 0) for i in range(1, 8)])
    expected = search(table, "21????")["items"]
    monkeypatch.setattr(compute, "USE_PRECOMPUTED", False)
    fallback = search(table, "21????")["items"]

    assert codes({"items": fallback}) == codes({"items": expected})
    for got, want in zip(fallback, expected):
        assert got["target_hex"] == want["target_hex"]
        assert got["delta_e"] == pytest.approx(want["delta_e"], abs=0.01)


def test_include_dyes_can_only_improve_the_match():
    table = owners(*[(i, f"u{i}", f"7F00{i:02X}", 0) for i in range(1, 8)])

    keys = [r["delta_e"] for r in search(table, "7F00??")["items"]]
    with_dyes = [r["delta_e"] for r in search(table, "7F00??", include_dyes=True)["items"]]

    assert sum(with_dyes) <= sum(keys)
