"""The pattern search, exercised without a DB.

_search takes the owner snapshot directly, so every case here is pure: build a
table with owner_hexes._build, ask for a page, assert on the rows.
"""
import numpy as np
import pytest

from app.errors import InvalidInputError
from app.services import color_index, compute
from app.services.hex_pattern import parse_pattern
from app.services.hex_search import _page, _search
from app.services.matching import get_tier
from app.services.owner_hexes import _build


def owners(*rows):
    """rows are (user_id, username, hex_color, piece)."""
    return _build([
        (uid, name, uid + 100, None, hex_color, piece)
        for uid, name, hex_color, piece in rows
    ])


def search(table, raw, **kwargs):
    kwargs.setdefault("include_dyes", False)
    kwargs.setdefault("owned_only", False)
    kwargs.setdefault("tier", None)
    kwargs.setdefault("pieces", None)
    kwargs.setdefault("limit", 10)
    kwargs.setdefault("offset", 0)
    return _search(table, parse_pattern(raw), **kwargs)


EMPTY = _build([])


class TestOrdering:
    def test_rows_ascend_by_delta_e(self):
        result = search(EMPTY, "21????", limit=20)

        des = [row["delta_e"] for row in result["items"]]
        assert des == sorted(des)

    def test_ties_break_on_the_colour_itself(self):
        """candidates ascend and the sort is stable, so equal ΔE keeps hex order.

        Driven directly because genuine float16 ties are rare in a real pattern,
        and rounding two distinct ΔE to 2dp only looks like one."""
        candidates = np.array([0x10, 0x20, 0x30, 0x40], dtype=np.uint32)
        de = np.array([5.0, 1.0, 1.0, 1.0], dtype=np.float32)

        assert _page(candidates, de, limit=3, offset=0).tolist() == [0x20, 0x30, 0x40]

    def test_the_tie_window_does_not_lose_rows_at_the_cut(self):
        """argpartition picks an arbitrary member of a tied group, so the cut
        value has to be widened to all of them before sorting."""
        candidates = np.array([0x10, 0x20, 0x30], dtype=np.uint32)
        de = np.array([2.0, 2.0, 2.0], dtype=np.float32)

        assert _page(candidates, de, limit=1, offset=1).tolist() == [0x20]

    def test_paging_does_not_repeat_or_skip(self):
        both = search(EMPTY, "21????", limit=2)["items"]
        first = search(EMPTY, "21????", limit=1, offset=0)["items"]
        second = search(EMPTY, "21????", limit=1, offset=1)["items"]

        assert [r["hex_code"] for r in first + second] == [r["hex_code"] for r in both]


class TestCounts:
    def test_pattern_total_is_the_unfiltered_size(self):
        assert search(EMPTY, "21????")["pattern_total"] == 16 ** 4

    def test_total_matches_pattern_total_when_nothing_filters(self):
        result = search(EMPTY, "21????")

        assert result["total"] == result["pattern_total"]

    def test_an_all_literal_pattern_returns_its_one_colour(self):
        result = search(EMPTY, "FF00AA")

        assert result["total"] == 1
        assert [r["hex_code"] for r in result["items"]] == ["FF00AA"]


class TestTier:
    def test_every_row_carries_the_requested_tier(self):
        result = search(EMPTY, "21????", tier="t1", limit=20)

        assert result["items"], "expected at least one t1 colour under 21????"
        assert all(row["tier"] == "t1" for row in result["items"])

    def test_the_tier_shown_is_the_one_the_delta_e_earns(self):
        """Pinned to matching.get_tier rather than to a copy of the bands."""
        for row in search(EMPTY, "21????", limit=20)["items"]:
            assert row["tier"] == get_tier(row["delta_e"])

    def test_a_tier_narrows_the_total(self):
        unfiltered = search(EMPTY, "21????")["total"]

        assert search(EMPTY, "21????", tier="t1")["total"] < unfiltered

    def test_all_is_not_a_filter(self):
        assert search(EMPTY, "21????", tier="all")["total"] == search(EMPTY, "21????")["total"]


class TestOwnership:
    def test_owners_annotate_rows_without_filtering_them(self):
        """A 16-colour pattern so the held colour is certain to be on the page."""
        table = owners((1, "holder", "210000", 0))

        result = search(table, "21000?", limit=16)

        assert result["total"] == 16
        row = next(r for r in result["items"] if r["hex_code"] == "210000")
        assert [o["username"] for o in row["owners"]] == ["holder"]
        assert sum(r["owner_count"] for r in result["items"]) == 1

    def test_owned_only_collapses_the_result_to_held_colours(self):
        table = owners(
            (1, "holder", "210000", 0),
            (2, "other", "21FFFF", 1),
            (3, "elsewhere", "990000", 0),
        )

        result = search(table, "21????", owned_only=True)

        assert result["total"] == 2
        assert {r["hex_code"] for r in result["items"]} == {"210000", "21FFFF"}
        assert result["pattern_total"] == 16 ** 4

    def test_owned_only_applies_before_paging(self):
        """Not page 0 of the whole space filtered -- the two differ entirely."""
        table = owners((1, "holder", "21FFFF", 0))

        held = search(table, "21????", owned_only=True, limit=1)["items"]
        unfiltered = search(table, "21????", limit=1)["items"]

        assert held[0]["hex_code"] == "21FFFF"
        assert unfiltered[0]["hex_code"] != "21FFFF"

    def test_a_colour_nobody_holds_still_appears_with_no_owners(self):
        result = search(EMPTY, "21????", limit=5)

        assert all(row["owner_count"] == 0 and row["owners"] == [] for row in result["items"])


class TestPieceFilter:
    """The piece filter is ownership-side only; ΔE and the catalog match are
    always global. These are the tests that stop it reaching the KD path."""

    def test_the_colour_rows_are_untouched_by_a_piece_filter(self):
        table = owners((1, "holder", "210000", 0))

        plain = search(table, "21000?", limit=16)["items"]
        filtered = search(table, "21000?", pieces=[3], limit=16)["items"]

        strip = lambda rows: [
            {k: v for k, v in r.items() if k not in ("owners", "owner_count")} for r in rows
        ]
        assert strip(plain) == strip(filtered)

    def test_only_the_owner_chips_change(self):
        table = owners(
            (1, "helmetonly", "210000", 0),
            (2, "bootsonly", "210000", 3),
        )

        result = search(table, "21000?", pieces=[3], limit=16)
        row = next(r for r in result["items"] if r["hex_code"] == "210000")

        assert [o["username"] for o in row["owners"]] == ["bootsonly"]

    def test_with_owned_only_a_piece_filter_drops_the_colour_entirely(self):
        table = owners((1, "helmetonly", "210000", 0))

        assert search(table, "21????", owned_only=True, pieces=[3])["total"] == 0

    def test_an_out_of_range_piece_matches_nothing(self):
        table = owners((1, "holder", "210000", 0))

        assert search(table, "21????", owned_only=True, pieces=[9])["total"] == 0


class TestUnconstrained:
    """??????, XYZWVU and ?????X all span the whole space."""

    @pytest.mark.parametrize("raw", ["??????", "XYZWVU", "?????X"])
    def test_the_whole_space_is_reported(self, raw):
        result = search(EMPTY, raw, limit=3)

        assert result["pattern_total"] == 0x1000000
        assert result["total"] == 0x1000000
        assert len(result["items"]) == 3

    def test_the_first_row_is_the_global_best_match(self):
        first = search(EMPTY, "??????", limit=1)["items"][0]

        assert first["delta_e"] == 0.0

    def test_the_ordered_index_and_the_gather_path_agree(self, monkeypatch):
        """Without hex_order_*.npy the same page has to come out of the ΔE table."""
        if not color_index.HAS_ORDER or not compute.USE_PRECOMPUTED:
            pytest.skip("needs both precomputed artifacts to compare them")

        expected = search(EMPTY, "??????", limit=5)["items"]
        monkeypatch.setattr(color_index, "HAS_ORDER", False)
        fallback = search(EMPTY, "??????", limit=5)["items"]

        assert [r["delta_e"] for r in fallback] == [r["delta_e"] for r in expected]

    def test_owned_only_never_touches_the_ordered_index(self, monkeypatch):
        table = owners((1, "holder", "210000", 0))
        monkeypatch.setattr(color_index, "HAS_ORDER", False)

        result = search(table, "??????", owned_only=True)

        assert result["total"] == 1
        assert result["items"][0]["hex_code"] == "210000"

    def test_with_no_artifacts_at_all_it_is_refused(self, monkeypatch):
        monkeypatch.setattr(color_index, "HAS_ORDER", False)
        monkeypatch.setattr(compute, "USE_PRECOMPUTED", False)

        with pytest.raises(InvalidInputError):
            search(EMPTY, "??????")


def test_the_kd_fallback_agrees_with_the_precomputed_tables(monkeypatch):
    """float16 vs float64 diverge in the last place, so compare at display
    precision -- the same 2dp the response rounds to."""
    if not compute.USE_PRECOMPUTED:
        pytest.skip("no precomputed tables to compare against")

    expected = search(EMPTY, "21????", limit=5)["items"]
    monkeypatch.setattr(compute, "USE_PRECOMPUTED", False)
    fallback = search(EMPTY, "21????", limit=5)["items"]

    assert [r["hex_code"] for r in fallback] == [r["hex_code"] for r in expected]
    assert [r["target_hex"] for r in fallback] == [r["target_hex"] for r in expected]
    for got, want in zip(fallback, expected):
        assert got["delta_e"] == pytest.approx(want["delta_e"], abs=0.01)


def test_include_dyes_can_change_the_nearest_match():
    keys = search(EMPTY, "7F00??", limit=20)["items"]
    with_dyes = search(EMPTY, "7F00??", include_dyes=True, limit=20)["items"]

    assert [r["delta_e"] for r in with_dyes] <= [r["delta_e"] for r in keys]


def test_generated_indices_are_not_materialised_for_the_whole_space():
    """A five-free-slot pattern is the real worst case; six must not generate."""
    assert parse_pattern("?????1").total == 16 ** 5
    assert search(EMPTY, "?????1", limit=1)["total"] == 16 ** 5
