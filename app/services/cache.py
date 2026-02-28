import json
import hashlib
from typing import Any
from redis.asyncio import Redis

GLOBAL_STATS_KEY = "global_stats"
GLOBAL_STATS_TTL = 3600

SEYMOUR_RESULTS_PREFIX = "seymours:"
SEYMOUR_RESULTS_TTL = 1800

USER_SEYMOURS_PREFIX = "user_seymours:"
USER_SEYMOURS_TTL = 60

USER_STATS_PREFIX = "user_stats:"
USER_STATS_TTL = 1800

RANKINGS_KEY = "rankings"
RANKINGS_TTL = 900

CUSTOM_SETS_PREFIX = "custom_sets:"
CUSTOM_SETS_TTL = 3600


def _hash_seymour_list(seymour_list: list) -> str:
    data = json.dumps(sorted(seymour_list), separators=(',', ':')).encode()
    return hashlib.md5(data).hexdigest()


def _custom_sets_key(seymour_hash: str) -> str:
    return f"{CUSTOM_SETS_PREFIX}{seymour_hash}"


def _user_seymours_key(user_id: int) -> str:
    return f"{USER_SEYMOURS_PREFIX}{user_id}"


def _seymour_results_key(user_id: int, filter_type: str, sub_filter: str | None, incomplete_only: bool, include_dyes: bool = False) -> str:
    sub = sub_filter or "none"
    return f"{SEYMOUR_RESULTS_PREFIX}{user_id}:{filter_type}:{sub}:{incomplete_only}:{include_dyes}"


def _seymour_filters_key(user_id: int, filter_type: str, incomplete_only: bool, include_dyes: bool = False) -> str:
    return f"{SEYMOUR_RESULTS_PREFIX}{user_id}:filters:{filter_type}:{incomplete_only}:{include_dyes}"


async def get_cached(redis: Redis, key: str) -> Any | None:
    cached = await redis.get(key)
    return json.loads(cached) if cached else None


async def set_cached(redis: Redis, key: str, data: Any, ttl: int) -> None:
    await redis.setex(key, ttl, json.dumps(data))


async def invalidate(redis: Redis, key: str) -> None:
    await redis.delete(key)


async def invalidate_pattern(redis: Redis, pattern: str) -> None:
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=pattern, count=100)
        if keys:
            await redis.delete(*keys)
        if cursor == 0:
            break


async def get_cached_seymour_results(
    redis: Redis,
    user_id: int,
    filter_type: str,
    sub_filter: str | None,
    incomplete_only: bool,
    include_dyes: bool = False
) -> list[dict] | None:
    key = _seymour_results_key(user_id, filter_type, sub_filter, incomplete_only, include_dyes)
    return await get_cached(redis, key)


async def set_cached_seymour_results(
    redis: Redis,
    user_id: int,
    filter_type: str,
    sub_filter: str | None,
    incomplete_only: bool,
    include_dyes: bool,
    results: list[dict]
) -> None:
    key = _seymour_results_key(user_id, filter_type, sub_filter, incomplete_only, include_dyes)
    await set_cached(redis, key, results, SEYMOUR_RESULTS_TTL)


async def get_cached_available_filters(
    redis: Redis,
    user_id: int,
    filter_type: str,
    incomplete_only: bool,
    include_dyes: bool = False
) -> list[str] | None:
    key = _seymour_filters_key(user_id, filter_type, incomplete_only, include_dyes)
    return await get_cached(redis, key)


async def set_cached_available_filters(
    redis: Redis,
    user_id: int,
    filter_type: str,
    incomplete_only: bool,
    include_dyes: bool,
    filters: list[str]
) -> None:
    key = _seymour_filters_key(user_id, filter_type, incomplete_only, include_dyes)
    await set_cached(redis, key, filters, SEYMOUR_RESULTS_TTL)


async def get_cached_user_seymours(redis: Redis, user_id: int) -> list[dict] | None:
    key = _user_seymours_key(user_id)
    cached = await redis.get(key)
    if cached:
        await redis.expire(key, USER_SEYMOURS_TTL)
        return json.loads(cached)
    return None


async def set_cached_user_seymours(redis: Redis, user_id: int, seymours: list[dict]) -> None:
    key = _user_seymours_key(user_id)
    await set_cached(redis, key, seymours, USER_SEYMOURS_TTL)


async def invalidate_user_seymours(redis: Redis, user_id: int) -> None:
    await invalidate_pattern(redis, f"{SEYMOUR_RESULTS_PREFIX}{user_id}:*")
    await invalidate(redis, _user_seymours_key(user_id))
    await invalidate(redis, _user_stats_key(user_id))


async def get_cached_global_stats(redis: Redis) -> dict | None:
    return await get_cached(redis, GLOBAL_STATS_KEY)


async def set_cached_global_stats(redis: Redis, stats: dict) -> None:
    await set_cached(redis, GLOBAL_STATS_KEY, stats, GLOBAL_STATS_TTL)


async def invalidate_global_stats(redis: Redis) -> None:
    await invalidate(redis, GLOBAL_STATS_KEY)


def _user_stats_key(user_id: int) -> str:
    return f"{USER_STATS_PREFIX}{user_id}"


async def get_cached_user_stats(redis: Redis, user_id: int) -> dict | None:
    return await get_cached(redis, _user_stats_key(user_id))


async def set_cached_user_stats(redis: Redis, user_id: int, stats: dict) -> None:
    await set_cached(redis, _user_stats_key(user_id), stats, USER_STATS_TTL)


async def invalidate_user_stats(redis: Redis, user_id: int) -> None:
    await invalidate(redis, _user_stats_key(user_id))


async def get_cached_rankings(redis: Redis) -> list[dict] | None:
    return await get_cached(redis, RANKINGS_KEY)


async def set_cached_rankings(redis: Redis, rankings: list[dict]) -> None:
    await set_cached(redis, RANKINGS_KEY, rankings, RANKINGS_TTL)

async def invalidate_rankings(redis: Redis) -> None:
    await invalidate(redis, RANKINGS_KEY)


async def get_cached_custom_sets(redis: Redis, seymour_list: list) -> list | None:
    key = _custom_sets_key(_hash_seymour_list(seymour_list))
    return await get_cached(redis, key)


async def set_cached_custom_sets(redis: Redis, seymour_list: list, custom_sets: list) -> None:
    key = _custom_sets_key(_hash_seymour_list(seymour_list))
    await set_cached(redis, key, custom_sets, CUSTOM_SETS_TTL)


RATE_LIMIT_PREFIX = "ratelimit:"


async def check_rate_limit(redis: Redis, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    full_key = f"{RATE_LIMIT_PREFIX}{key}"
    current = await redis.incr(full_key)
    if current == 1:
        await redis.expire(full_key, window_seconds)
    ttl = await redis.ttl(full_key)
    return current <= max_requests, max(0, ttl)
