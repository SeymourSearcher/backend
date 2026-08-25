import numpy as np
import plotly.graph_objects as go

from app.pieces import PIECE_COUNT, Piece
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


def generate_color_mask(
    min_de: float,
    max_de: float,
    input_hex_codes: list[str] | None,
    input_seymours: list[tuple[str, Piece]] | None,
    include_dyes: bool
) -> tuple[np.ndarray, int]:
    if input_seymours:
        indices, distances = compute_distances_with_pieces(input_seymours, include_dyes)
    else:
        indices, distances = compute_distances(input_hex_codes, include_dyes)
    
    mask = (distances >= min_de) & (distances < max_de)
    return indices[mask], len(indices)


def get_stats(matching_count: int, total_count: int) -> dict:
    return {
        "count": matching_count,
        "total": total_count,
        "percentage": round(matching_count / total_count * 100, 4) if total_count > 0 else 0
    }


def generate_3d_html(indices: np.ndarray, total: int, max_points: int = 100000) -> str:
    n_points = len(indices)
    
    if n_points == 0:
        return "<html><body><h2>No matching colors found</h2></body></html>"
    
    if n_points > max_points:
        sampled = indices[np.random.choice(n_points, max_points, replace=False)]
    else:
        sampled = indices
    
    r = (sampled >> 16) & 0xFF
    g = (sampled >> 8) & 0xFF
    b = sampled & 0xFF
    
    colors = np.column_stack([r, g, b])
    color_strs = [f'rgb({c[0]},{c[1]},{c[2]})' for c in colors]
    
    fig = go.Figure(data=[go.Scatter3d(
        x=r, y=g, z=b,
        mode='markers',
        marker=dict(size=1.5, color=color_strs, opacity=0.7),
        hoverinfo='skip',
    )])
    
    fig.update_layout(
        scene=dict(
            xaxis_title='Red',
            yaxis_title='Green',
            zaxis_title='Blue',
            xaxis=dict(range=[0, 255]),
            yaxis=dict(range=[0, 255]),
            zaxis=dict(range=[0, 255]),
        ),
        title=f'Color Cube ({len(sampled):,} points sampled from {n_points:,})',
        margin=dict(l=0, r=0, t=40, b=0)
    )
    
    config = {
        'scrollZoom': True,
    }
    
    return fig.to_html(full_html=True, include_plotlyjs='cdn', config=config)
