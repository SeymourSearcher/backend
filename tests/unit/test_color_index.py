"""The ordered index replaces a per-request KD query, so the bands it returns
have to be the ones that query would have produced -- not merely the same size.
A precision mismatch between the stored ordering and the boundary probe puts a
handful of colours on the wrong side of an edge and nothing else notices.
"""
import base64
import re

import numpy as np
import pytest

from app.services import color_index, visualization as viz
from app.services.color_index import ColorSelection

pytestmark = pytest.mark.skipif(
    not color_index.HAS_ORDER,
    reason="hex_order_*.npy not generated; see scripts/precompute_hex_order.py",
)

BANDS = [(0.0, 2.0), (1.5, 3.7)]


def live_band(min_de, max_de, include_dyes):
    """What the old generate_color_mask computed, by querying the tree."""
    indices, distances = viz.compute_distances(None, include_dyes)
    return indices[(distances >= min_de) & (distances < max_de)]


@pytest.mark.parametrize("include_dyes", [True, False])
@pytest.mark.parametrize("min_de,max_de", BANDS)
def test_band_holds_exactly_the_colours_the_tree_query_finds(min_de, max_de, include_dyes):
    selection = viz.select_colors(min_de, max_de, None, None, include_dyes)
    selected = np.asarray(selection.source[selection.lo:selection.hi])

    assert np.array_equal(np.sort(selected), np.sort(live_band(min_de, max_de, include_dyes)))
    assert selection.total == 0x1000000


def test_an_empty_band_selects_nothing():
    assert viz.select_colors(7.0, 7.0, None, None, True).count == 0


def test_a_band_past_every_distance_selects_everything():
    assert viz.select_colors(0.0, 500.0, None, None, True).count == 0x1000000


def test_scoped_requests_still_go_through_the_tree(monkeypatch):
    """The ordering only answers the unscoped question; per-user input must not hit it."""
    monkeypatch.setattr(color_index, "band", lambda *a: pytest.fail("scoped request used the band"))

    hexes = ["FFFF00", "000000", "AABBCC"]
    assert viz.select_colors(0.0, 500.0, hexes, None, True).count == len(hexes)
    assert viz.select_colors(0.0, 500.0, None, [(h, 0) for h in hexes], True).count == len(hexes)


def test_the_unscoped_path_falls_back_when_the_artifact_is_missing(monkeypatch):
    monkeypatch.setattr(color_index, "HAS_ORDER", False)
    selection = viz.select_colors(0.0, 2.0, None, None, True)

    assert np.array_equal(
        np.sort(np.asarray(selection.source[selection.lo:selection.hi])),
        np.sort(live_band(0.0, 2.0, True)),
    )


def test_sample_returns_the_whole_selection_when_it_fits():
    selection = ColorSelection.of(np.array([5, 6, 7], dtype=np.uint32), 10)

    assert sorted(selection.sample(3, np.random.default_rng(0)).tolist()) == [5, 6, 7]


def test_sample_draws_without_replacement_from_within_the_slice():
    source = np.arange(100, dtype=np.uint32)
    sampled = ColorSelection(source, 10, 60, 100).sample(20, np.random.default_rng(0))

    assert len(sampled) == 20
    assert len(set(sampled.tolist())) == 20
    assert set(sampled.tolist()) <= set(range(10, 60))


def decode_points(html: str) -> np.ndarray:
    blob = re.search(r'atob\("([A-Za-z0-9+/=]*)"\)', html).group(1)
    return np.frombuffer(base64.b64decode(blob), dtype="<u4")


def test_the_page_carries_the_sampled_colours_as_packed_little_endian_uint32():
    selection = viz.select_colors(0.0, 2.0, None, None, True)
    points = decode_points(viz.generate_3d_html(selection, 5_000))

    assert len(points) == 5_000
    assert set(points.tolist()) <= set(np.asarray(selection.source[selection.lo:selection.hi]).tolist())


def test_a_selection_smaller_than_max_points_is_sent_whole():
    selection = ColorSelection.of(np.array([0x000000, 0xFFFFFF, 0x4080C0], dtype=np.uint32), 10)

    assert sorted(decode_points(viz.generate_3d_html(selection, 5_000)).tolist()) == [
        0x000000, 0x4080C0, 0xFFFFFF
    ]


def test_an_empty_selection_renders_the_placeholder():
    assert viz.generate_3d_html(ColorSelection.of(np.array([], dtype=np.uint32), 0)) == viz.EMPTY_HTML
