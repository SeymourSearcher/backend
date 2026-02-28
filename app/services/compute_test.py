import numpy as np
from scipy.spatial import cKDTree
from compute import ALL_LAB, KEY_ITEM_LABS, DYE_LABS

def generate_color_map(threshold: float = 5.0) -> np.ndarray:
    """Returns boolean mask where True = delta E < threshold to any key item."""
    tree = cKDTree(np.vstack([KEY_ITEM_LABS]))
    distances, _ = tree.query(ALL_LAB, k=1, workers=-1)
    return distances < threshold


def get_matching_hexes(threshold: float = 5.0) -> list[str]:
    """Returns list of hex codes with delta E < threshold."""
    return [f'{i:06X}' for i in np.where(generate_color_map(threshold))[0]]


def visualize_color_map(mask: np.ndarray, output_path: str = "color_map.png"):
    """Visualize the color map as a 4096x4096 image."""
    from PIL import Image
    
    img = np.zeros((4096, 4096, 3), dtype=np.uint8)
    indices = np.where(mask)[0]
    
    r = (indices >> 16) & 0xFF
    g = (indices >> 8) & 0xFF
    b = indices & 0xFF
    rows = r * 16 + (g >> 4)
    cols = (g & 0xF) * 256 + b
    
    img[rows, cols] = np.column_stack([r, g, b])
    
    Image.fromarray(img).save(output_path)
    print(f"Saved to {output_path}")


def visualize_color_cube_slices(mask: np.ndarray, output_path: str = "color_cube.png", slices: int = 16):
    """Visualize RGB cube as grid of R slices showing G vs B."""
    from PIL import Image
    
    grid = int(np.ceil(np.sqrt(slices)))
    img = np.zeros((grid * 256, grid * 256, 3), dtype=np.uint8)
    
    for i in range(slices):
        r = int(i * 255 / (slices - 1))
        row_off = (i // grid) * 256
        col_off = (i % grid) * 256
        
        base = r << 16
        g_vals = np.arange(256)
        b_vals = np.arange(256)
        rgb_slice = base + (g_vals[:, None] << 8) + b_vals[None, :]
        
        slice_mask = mask[rgb_slice]
        gy, bx = np.where(slice_mask)
        img[row_off + gy, col_off + bx] = [r, gy, bx] if len(gy) == 0 else np.column_stack([np.full_like(gy, r), gy, bx])
    
    Image.fromarray(img).save(output_path)
    print(f"Saved to {output_path}")


def visualize_color_cube_3d(mask: np.ndarray, output_path: str = "color_cube_3d.html", max_points: int = 50000):
    """Visualize RGB cube as interactive 3D scatter plot with smart downsampling."""
    import plotly.graph_objects as go
    
    indices = np.where(mask)[0]
    n_points = len(indices)
    
    if n_points > max_points:
        step = n_points // max_points
        indices = indices[::step]
    
    r = (indices >> 16) & 0xFF
    g = (indices >> 8) & 0xFF
    b = indices & 0xFF
    
    colors = np.column_stack([r, g, b])
    color_strs = [f'rgb({c[0]},{c[1]},{c[2]})' for c in colors]
    
    fig = go.Figure(data=[go.Scatter3d(
        x=r, y=g, z=b,
        mode='markers',
        marker=dict(size=1.5, color=color_strs, opacity=0.7),
        hovertemplate='R:%{x} G:%{y} B:%{z}<extra></extra>'
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
        title=f'Color Cube ({len(indices):,} points sampled from {n_points:,})',
        margin=dict(l=0, r=0, t=40, b=0)
    )
    
    fig.write_html(output_path)
    print(f"Saved to {output_path} ({len(indices):,} points)")


if __name__ == "__main__":
    import time
    
    print("Computing color map (dE < 5)...")
    start = time.time()
    
    mask = generate_color_map(2.0)
    
    print(f"Completed in {time.time() - start:.2f}s")
    print(f"Matching colors: {mask.sum():,} / 16,777,216")
    
    print("\nGenerating visualizations...")
    visualize_color_map(mask, "color_map.png")
    visualize_color_cube_slices(mask, "color_cube.png")
    visualize_color_cube_3d(mask, "color_cube_3d.html", max_points=500000)