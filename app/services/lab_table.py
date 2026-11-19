"""CIELAB for every 24-bit RGB colour, indexed by the colour's integer value.

16.7M x 3 float32 = 192 MiB. Built at image build time by
scripts/precompute_lab_table.py and memory-mapped, so the pages are shared,
reclaimable page cache instead of anonymous memory in every process, and import
skips a ~1.4s build that peaked ~830 MiB above baseline.
"""
import os

import numpy as np

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'static')
LAB_TABLE_PATH = os.path.join(STATIC_DIR, 'lab_table.npy')


def build_lab_table() -> np.ndarray:
    rgb = np.arange(0x1000000, dtype=np.uint32)
    r = ((rgb >> 16) & 0xFF).astype(np.float32) / 255.0
    g = ((rgb >> 8) & 0xFF).astype(np.float32) / 255.0
    b = (rgb & 0xFF).astype(np.float32) / 255.0

    def to_linear(c):
        mask = c > 0.04045
        c[mask] = ((c[mask] + 0.055) / 1.055) ** 2.4
        c[~mask] /= 12.92
        return c

    r, g, b = to_linear(r), to_linear(g), to_linear(b)

    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    del r, g, b

    def f(t):
        mask = t > 0.008856
        t[mask] **= (1/3)
        t[~mask] = 7.787 * t[~mask] + 16/116
        return t

    fx, fy, fz = f(x), f(y), f(z)

    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b_out = 200 * (fy - fz)

    return np.column_stack([L, a, b_out]).astype(np.float32)


def load_lab_table() -> np.ndarray:
    """Read-only either way, so a stray in-place write fails in dev as it would on the mmap."""
    if os.path.exists(LAB_TABLE_PATH):
        return np.load(LAB_TABLE_PATH, mmap_mode='r')
    table = build_lab_table()
    table.flags.writeable = False
    return table
