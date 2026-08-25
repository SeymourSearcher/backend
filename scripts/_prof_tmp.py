import cProfile
import os
import pstats
import sys
import time

for k, v in {"DATABASE_URL": "mysql+aiomysql://u:p@127.0.0.1:1/d", "JWT_SECRET": "b",
             "DISCORD_CLIENT_ID": "b", "DISCORD_CLIENT_SECRET": "b",
             "DISCORD_REDIRECT_URI": "http://l/cb"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, "/bench")

import numpy as np
from app.services.compute import KEY_ITEM_HEXES, find_custom_sets


def make_inventory(n, seed=0):
    rng = np.random.default_rng(seed)
    targets = [h for h, _ in KEY_ITEM_HEXES]
    out = []
    for _ in range(n):
        base = int(targets[rng.integers(len(targets))], 16)
        r, g, b = (base >> 16) & 0xFF, (base >> 8) & 0xFF, base & 0xFF
        j = rng.integers(-6, 7, size=3)
        r, g, b = (int(np.clip(v + d, 0, 255)) for v, d in zip((r, g, b), j))
        out.append((f"{r:02X}{g:02X}{b:02X}", int(rng.integers(4))))
    return out


print("scaling:")
for n in (10_000, 17_581, 30_000):
    inv = make_inventory(n)
    t = time.perf_counter()
    res = find_custom_sets(inv, max_avg_delta_e=1.0)
    print(f"  n={n:>6,}  {time.perf_counter() - t:>7.2f}s   sets={len(res):,}")

inv = make_inventory(10_000)
pr = cProfile.Profile()
pr.enable()
find_custom_sets(inv, max_avg_delta_e=1.0)
pr.disable()
print("\nprofile at n=10,000:")
pstats.Stats(pr).sort_stats("cumulative").print_stats(14)
