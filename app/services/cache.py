"""Redis-backed caches, one CacheEntry per namespace.

An entry owns its key prefix and TTL together; previously the two were declared
apart and paired by hand in a get/set wrapper per namespace.
"""
import hashlib
import json
from typing import Any

from redis.asyncio import Redis


class CacheEntry:
    def __init__(self, prefix: str, ttl: int, *, refresh_on_read: bool = False):
        self.prefix = prefix
        self.ttl = ttl
        self.refresh_on_read = refresh_on_read

    def key(self, *parts: Any) -> str:
        return self.prefix + ":".join(str(p) for p in parts)

    async def get(self, redis: Redis, *parts: Any) -> Any | None:
        key = self.key(*parts)
        cached = await redis.get(key)
        if not cached:
            return None
        if self.refresh_on_read:
            await redis.expire(key, self.ttl)
        return json.loads(cached)

    async def set(self, redis: Redis, value: Any, *parts: Any) -> None:
        await redis.setex(self.key(*parts), self.ttl, json.dumps(value))

    async def invalidate(self, redis: Redis, *parts: Any) -> None:
        await redis.delete(self.key(*parts))

    async def invalidate_all(self, redis: Redis, *parts: Any) -> None:
        """Every key under this namespace, or under a prefix of its parts."""
        pattern = f"{self.key(*parts)}*" if parts else f"{self.prefix}*"
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=100)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break


GLOBAL_STATS = CacheEntry("global_stats", 3600)
RANKINGS = CacheEntry("rankings", 900)
USER_STATS = CacheEntry("user_stats:", 1800)
CUSTOM_SETS = CacheEntry("custom_sets:", 3600)
HEX_INFO = CacheEntry("hex_info:", 1800)

# Kept warm while a page is being browsed rather than expiring mid-session.
USER_SEYMOURS = CacheEntry("user_seymours:", 60, refresh_on_read=True)

# Results and filters deliberately share a prefix so invalidating a user's
# seymours clears both in one scan.
SEYMOUR_RESULTS = CacheEntry("seymours:", 1800)
SEYMOUR_FILTERS = CacheEntry("seymours:", 1800)


def custom_sets_hash(seymour_list: list) -> str:
    data = json.dumps(sorted(seymour_list), separators=(',', ':')).encode()
    return hashlib.md5(data).hexdigest()


async def invalidate_user_seymours(redis: Redis, user_id: int) -> None:
    await SEYMOUR_RESULTS.invalidate_all(redis, user_id, "")
    await USER_SEYMOURS.invalidate(redis, user_id)
    await USER_STATS.invalidate(redis, user_id)


RATE_LIMIT_PREFIX = "ratelimit:"


async def check_rate_limit(redis: Redis, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    full_key = f"{RATE_LIMIT_PREFIX}{key}"
    current = await redis.incr(full_key)
    if current == 1:
        await redis.expire(full_key, window_seconds)
    ttl = await redis.ttl(full_key)
    return current <= max_requests, max(0, ttl)
