from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.compute import find_matching_seymours, find_sets
from app.services.compute import KEY_ITEM_HEXES, HEX_WORDS
from app.services.compute import seymour_converter
from app.services.cache import GLOBAL_STATS, RANKINGS
from app.services.matching import get_tier

# Must stay in step with StatsResponse; test_stats.py pins them together.
STAT_KEYS = (
    "stars", "t1", "t2", "t3", "words", "dupes",
    "total_sets", "dyed_sets", "total_seymours",
)


def count_dupes(seymour_list):
    counts = Counter(seymour_list)
    return sum(c - 1 for c in counts.values() if c > 1)

HEX_SEQUENCE = "0123456789ABCDEF"
HEX_SEQUENCE_REV = HEX_SEQUENCE[::-1]

def _is_sequential(h):
    return h in HEX_SEQUENCE or h in HEX_SEQUENCE_REV

def _is_repeating(h):
    return h[0:2] == h[2:4] == h[4:6]

def _is_aabbcc(h):
    return h[0] == h[1] and h[2] == h[3] and h[4] == h[5]

def _is_triple_pattern(h):
    return h[0] == h[2] == h[4] and h[1] == h[3] == h[5]

def _is_palindrome(h):
    return h == h[::-1]

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
    counts = Counter(get_tier(match[2]) for match in matches)
    return counts["t1"], counts["t2"], counts["t3"]

def get_stats(seymour_list):
    """Compute all stats in parallel using ThreadPoolExecutor."""
    futures = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures['matches'] = executor.submit(
            find_matching_seymours, seymour_list, KEY_ITEM_HEXES, threshold=10.0, best_only=True
        )
        futures['sets'] = executor.submit(find_sets, seymour_list)
        futures['stars'] = executor.submit(count_stars, seymour_list)
        futures['words'] = executor.submit(count_words, seymour_list)
        futures['dupes'] = executor.submit(count_dupes, seymour_list)
        
        matches = futures['matches'].result()
        sets, dyed_sets = futures['sets'].result()
        stars = futures['stars'].result()
        words = futures['words'].result()
        dupes = futures['dupes'].result()
    
    t1, t2, t3 = get_tier_data(matches)
    
    return {
        "stars": stars,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "words": words,
        "dupes": dupes,
        "total_sets": len(sets),
        "dyed_sets": len(dyed_sets),
        "total_seymours": len(seymour_list),
    }




def sum_rankings_stats(rankings: list[dict]) -> dict:
    """Global stats are just the per-user stats added up."""
    return {key: sum(r[key] for r in rankings) for key in STAT_KEYS}


async def get_global_stats(redis: Redis, db: AsyncSession) -> dict:
    cached = await GLOBAL_STATS.get(redis)
    if cached:
        return cached

    cached_rankings = await RANKINGS.get(redis)
    if cached_rankings:
        stats = sum_rankings_stats(cached_rankings)
        await GLOBAL_STATS.set(redis, stats)
        return stats

    # Deferred: app.services.rankings imports this module at import time.
    from app.services.rankings import compute_and_cache_rankings
    return sum_rankings_stats(await compute_and_cache_rankings(db, redis))
