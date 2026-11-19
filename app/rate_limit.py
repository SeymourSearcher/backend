"""Per-IP rate limiting: the counter, the caller's identity, and the wrappers.

A limit is declared as a RateLimit next to the endpoint it guards, so the numbers
sit with the reasoning for them, and applied one of two ways:

    router = APIRouter(dependencies=[Depends(limiter(HEX_LIMIT))])   # fixed
    await enforce(GENERATE_SCOPED, request, redis)                   # chosen per request

app/main.py applies `global_rate_limit` to every route as a backstop under all
of them.
"""
import ipaddress
import logging
from typing import Annotated, Callable, NamedTuple

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.dependencies import get_current_user, get_redis
from app.models import User

logger = logging.getLogger(__name__)

KEY_PREFIX = "ratelimit:"


class RateLimit(NamedTuple):
    """`limit` requests per `window` seconds per caller, under one Redis bucket."""
    bucket: str
    limit: int
    window: int


def client_ip(request: Request) -> str:
    """The caller's own address rather than the proxy's.

    Gotcha: both sources are caller-controlled headers, so this is only honest
    while Cloudflare is the sole route to the origin. If the origin ever becomes
    directly reachable, an attacker rotates CF-Connecting-IP and every limit here
    becomes unenforceable. uvicorn needs --proxy-headers for the request.client
    fallback to be the client and not traefik; see the Dockerfile.
    """
    forwarded = request.headers.get("CF-Connecting-IP")
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


async def _consume(redis: Redis, key: str, rl: RateLimit) -> tuple[bool, int]:
    """Fixed window. The key is created with its TTL before it is counted, in one
    transaction, so no failure can leave a counter that never expires."""
    full_key = f"{KEY_PREFIX}{key}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(full_key, 0, ex=rl.window, nx=True)
        pipe.incr(full_key)
        pipe.ttl(full_key)
        _, current, ttl = await pipe.execute()
    return current <= rl.limit, max(0, ttl)


async def _enforce_for(rl: RateLimit, identity: str, redis: Redis) -> None:
    try:
        allowed, retry_after = await _consume(redis, f"{rl.bucket}:{identity}", rl)
    except RedisError:
        # Fail open. Redis being unreachable is an outage of the limiter, not of
        # the API, and every endpoint behind it already tolerates a cache miss.
        logger.warning("rate limiter unavailable, allowing request", exc_info=True)
        return
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
        )


def is_internal(request: Request) -> bool:
    """A caller on the server's own network, i.e. the Next.js server rendering pages.

    Gotcha: only sound while Cloudflare is the sole public route in, as it sets
    CF-Connecting-IP on every request it proxies. Without that header, a private
    peer address cannot be an outside caller.
    """
    if request.headers.get("CF-Connecting-IP") or not request.client:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_private
    except ValueError:
        return False


async def enforce(rl: RateLimit, request: Request, redis: Redis) -> None:
    if is_internal(request):
        return
    await _enforce_for(rl, client_ip(request), redis)


def limiter(rl: RateLimit) -> Callable:
    """`rl` as a FastAPI dependency, for a whole router or a single route."""
    async def dependency(
        request: Request, redis: Annotated[Redis, Depends(get_redis)]
    ) -> None:
        await enforce(rl, request, redis)

    return dependency


def user_limiter(rl: RateLimit) -> Callable:
    """`rl` per signed-in account, so rotating addresses doesn't reset it."""
    async def dependency(
        user: Annotated[User, Depends(get_current_user)],
        redis: Annotated[Redis, Depends(get_redis)],
    ) -> None:
        await _enforce_for(rl, f"user:{user.id}", redis)

    return dependency


# A ceiling on any one address, not a per-endpoint budget: the caches in front of
# the expensive endpoints are all keyed by caller-supplied values -- hex code,
# auction filters -- so a scan misses every time and puts a full walk on the
# thread pool plus a fresh Redis key. High enough that no real page load notices.
GLOBAL_LIMIT = RateLimit("global", 240, 60)

# The swarm healthcheck runs every 10s and shares an address with anything else
# calling from inside the container.
UNLIMITED_PATHS = frozenset({"/health", "/robots.txt"})


async def global_rate_limit(
    request: Request, redis: Annotated[Redis, Depends(get_redis)]
) -> None:
    if request.url.path in UNLIMITED_PATHS:
        return
    await enforce(GLOBAL_LIMIT, request, redis)
