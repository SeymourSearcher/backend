import base64
import json
from string import Template

import numpy as np
from plotly.offline import get_plotlyjs_version

from app.pieces import PIECE_COUNT, Piece
from app.services import color_index
from app.services.color_index import ColorSelection
from app.services.compute import (
    ALL_ITEM_TREE,
    ALL_LAB,
    ALL_PIECE_TREES,
    KEY_ITEM_TREE,
    KEY_PIECE_TREES,
    _hex_list_to_ints,
)


def compute_distances(input_hex_codes: list[str] | None, include_dyes: bool) -> tuple[np.ndarray, np.ndarray]:
    # ALL_ITEM_TREE covers the same point set as KEY_ITEM_LABS + DYE_LABS, so
    # nearest-neighbour distances are unchanged by reusing the prebuilt trees.
    tree = ALL_ITEM_TREE if include_dyes else KEY_ITEM_TREE

    if input_hex_codes:
        input_ints = _hex_list_to_ints(input_hex_codes)
        input_labs = ALL_LAB[input_ints]
        distances, _ = tree.query(input_labs, k=1, workers=-1)
        return input_ints, distances
    else:
        distances, _ = tree.query(ALL_LAB, k=1, workers=-1)
        return np.arange(len(ALL_LAB), dtype=np.uint32), distances


def compute_distances_with_pieces(
    input_seymours: list[tuple[str, Piece]],
    include_dyes: bool
) -> tuple[np.ndarray, np.ndarray]:
    trees = ALL_PIECE_TREES if include_dyes else KEY_PIECE_TREES

    all_indices = []
    all_distances = []

    for piece_idx in range(PIECE_COUNT):
        tree = trees[piece_idx]
        if tree is None:
            continue

        hex_codes = [h for h, p in input_seymours if p == piece_idx]
        if not hex_codes:
            continue

        input_ints = _hex_list_to_ints(hex_codes)
        distances, _ = tree.query(ALL_LAB[input_ints], k=1, workers=-1)

        all_indices.append(input_ints)
        all_distances.append(distances)

    if not all_indices:
        return np.array([], dtype=np.uint32), np.array([], dtype=np.float32)

    return np.concatenate(all_indices), np.concatenate(all_distances)


def select_colors(
    min_de: float,
    max_de: float,
    input_hex_codes: list[str] | None,
    input_seymours: list[tuple[str, Piece]] | None,
    include_dyes: bool
) -> ColorSelection:
    """Colours whose nearest target lies in [min_de, max_de).

    The unscoped case is answered from the ordered index rather than by querying
    the tree, because its result depends on nothing the caller supplied. See
    app/services/color_index.py.
    """
    unscoped = not input_seymours and not input_hex_codes
    if unscoped and color_index.HAS_ORDER:
        return color_index.band(min_de, max_de, include_dyes)

    if input_seymours:
        indices, distances = compute_distances_with_pieces(input_seymours, include_dyes)
    else:
        indices, distances = compute_distances(input_hex_codes, include_dyes)

    mask = (distances >= min_de) & (distances < max_de)
    return ColorSelection.of(indices[mask], len(indices))


def get_stats(matching_count: int, total_count: int) -> dict:
    return {
        "count": matching_count,
        "total": total_count,
        "percentage": round(matching_count / total_count * 100, 4) if total_count > 0 else 0
    }


EMPTY_HTML = "<html><body><h2>No matching colors found</h2></body></html>"

# One packed uint32 per point rather than four fields: x, y, z and the marker
# colour are the same 24 bits, so sending them separately costs 6x the payload
# and pays plotly's per-element validation of a 200k-long colour list -- 4.4s of
# a 6s request. The page expands them itself; it already needs JS and the CDN.
_PAGE = Template("""<!doctype html>
<html><head><meta charset="utf-8"><title>$title</title>
<script src="https://cdn.plot.ly/plotly-$plotly_version.min.js"></script></head>
<body style="margin:0"><div id="cube" style="width:100vw;height:100vh"></div>
<script>
// Written little-endian; Uint32Array reads native, which is little-endian on
// every platform a browser runs on.
const bytes = Uint8Array.from(atob("$packed"), c => c.charCodeAt(0));
const points = new Uint32Array(bytes.buffer);
const n = points.length;
const x = new Uint8Array(n), y = new Uint8Array(n), z = new Uint8Array(n);
const color = new Array(n);
for (let i = 0; i < n; i++) {
  const p = points[i], r = (p >> 16) & 255, g = (p >> 8) & 255, b = p & 255;
  x[i] = r; y[i] = g; z[i] = b;
  color[i] = "rgb(" + r + "," + g + "," + b + ")";
}
Plotly.newPlot("cube", [{
  type: "scatter3d", x: x, y: y, z: z, mode: "markers", hoverinfo: "skip",
  marker: {size: 1.5, opacity: 0.7, color: color}
}], $layout, {scrollZoom: true});
</script></body></html>
""")


def _layout(title: str) -> str:
    return json.dumps({
        "scene": {
            "xaxis": {"title": {"text": "Red"}, "range": [0, 255]},
            "yaxis": {"title": {"text": "Green"}, "range": [0, 255]},
            "zaxis": {"title": {"text": "Blue"}, "range": [0, 255]},
        },
        "title": {"text": title},
        "margin": {"l": 0, "r": 0, "t": 40, "b": 0},
    })


def generate_3d_html(selection: ColorSelection, max_points: int = 100000) -> str:
    if selection.count == 0:
        return EMPTY_HTML

    sampled = selection.sample(max_points, np.random.default_rng())
    title = f"Color Cube ({len(sampled):,} points sampled from {selection.count:,})"

    return _PAGE.substitute(
        title=title,
        plotly_version=get_plotlyjs_version(),
        packed=base64.b64encode(np.ascontiguousarray(sampled, dtype="<u4").tobytes()).decode(),
        layout=_layout(title),
    )
