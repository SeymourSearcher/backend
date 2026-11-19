"""
Write the RGB -> LAB table for all 16.7M colours to app/static/lab_table.npy.

Run before scripts/precompute_hex_matches.py and precompute_hex_order.py: both
import app.services.compute, which maps this file when it exists.
See app/services/lab_table.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np

from app.services.lab_table import LAB_TABLE_PATH, build_lab_table

if __name__ == "__main__":
    table = build_lab_table()
    np.save(LAB_TABLE_PATH, table)
    print(f"Saved: {LAB_TABLE_PATH} ({table.nbytes / 1024 / 1024:.0f} MB)")
