import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from redis.asyncio import Redis

from app.services.compute import find_matching_seymours, find_sets, find_custom_sets
from app.services.compute import KEY_ITEM_HEXES, HEX_WORDS
from app.services.compute import seymour_converter
from app.services.cache import (
    get_cached_global_stats, set_cached_global_stats,
    get_cached_custom_sets, set_cached_custom_sets,
    set_cached_user_stats
)
from app.models import Seymour

_executor = ThreadPoolExecutor(max_workers=2)

def count_dupes(seymour_list):
    counts = Counter(seymour_list)
    return sum(c - 1 for c in counts.values() if c > 1)

def _is_sequential(h):
    return h == "123456" or h == "654321" or h == "012345" or h == "543210"

def _is_repeating(h):
    return h[0:2] == h[2:4] == h[4:6]

def _is_aabbcc(h):
    return h[0] == h[1] and h[2] == h[3] and h[4] == h[5]

def _is_triple_pattern(h):
    return h[0] == h[2] == h[4] and h[1] == h[3] == h[5]

def _contains_word(h):
    for word in HEX_WORDS:
        if word in h:
            return True
    return False

def is_star(hex_code):
    h = hex_code.upper()
    return _is_sequential(h) or _is_repeating(h) or _is_aabbcc(h) or _is_triple_pattern(h)

def count_stars(seymour_list):
    return sum(1 for hex_code, _ in seymour_list if is_star(hex_code))

def count_words(seymour_list):
    return sum(1 for hex_code, _ in seymour_list if _contains_word(hex_code.upper()))

def get_tier_data(matches):
    t1 = 0
    t2 = 0
    t3 = 0
    for match in matches:
        if match[2] < 2:
            t1 += 1
        elif match[2] < 5:
            t2 += 1
        elif match[2] < 10:
            t3 += 1
    return t1, t2, t3

def get_stats(seymour_list, custom_sets=None):
    matches = find_matching_seymours(seymour_list, KEY_ITEM_HEXES, threshold=10.0, best_only=True)
    t1, t2, t3 = get_tier_data(matches)
    sets, dyed_sets = find_sets(seymour_list)
    if custom_sets is None:
        custom_sets = find_custom_sets(seymour_list)
    return {
        "stars": count_stars(seymour_list),
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "words": count_words(seymour_list),
        "dupes": count_dupes(seymour_list),
        "total_sets": len(sets),
        "dyed_sets": len(dyed_sets),
        "custom_sets": len(custom_sets),
        "total_seymours": len(seymour_list),
    }


def _compute_custom_sets_sync(seymour_list):
    return find_custom_sets(seymour_list)


async def get_cached_or_compute_custom_sets(redis: Redis, seymour_list: list) -> list:
    cached = await get_cached_custom_sets(redis, seymour_list)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    custom_sets = await loop.run_in_executor(_executor, _compute_custom_sets_sync, seymour_list)
    await set_cached_custom_sets(redis, seymour_list, custom_sets)
    return custom_sets


def _schedule_custom_sets_background(redis: Redis, seymour_list: list, user_id: int | None = None):
    async def _bg_task():
        custom_sets = await get_cached_or_compute_custom_sets(redis, seymour_list)
        stats = get_stats(seymour_list, custom_sets=custom_sets)
        if user_id is not None:
            await set_cached_user_stats(redis, user_id, stats)
        else:
            await set_cached_global_stats(redis, stats)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_bg_task())
    except RuntimeError:
        pass


async def get_global_stats(db: AsyncSession, redis: Redis) -> dict:
    cached = await get_cached_global_stats(redis)
    if cached:
        return cached
    
    result = await db.execute(select(Seymour))
    seymours = result.scalars().all()
    seymour_list = [(s.hex_color, s.item_name) for s in seymours]
    
    cached_custom = await get_cached_custom_sets(redis, seymour_list)
    if cached_custom is not None:
        stats = get_stats(seymour_list, custom_sets=cached_custom)
    else:
        _schedule_custom_sets_background(redis, seymour_list)
        stats = get_stats(seymour_list, custom_sets=[])
    
    await set_cached_global_stats(redis, stats)
    return stats


if __name__ == "__main__":
    import sys
    import os
    # Add backend to path so absolute imports work
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    
    from app.services.parser import parse_seymour_file
    with open(r"C:\Users\Hexay\Downloads\AllyMe's Export 2.9.2026.txt", 'r') as f:
        content = f.read()
        parsed, skipped = parse_seymour_file(content)
        seymour_list = seymour_converter(parsed)
        
    print(get_stats(seymour_list))