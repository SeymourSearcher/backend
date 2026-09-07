import logging
from typing import Annotated, NamedTuple, Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.database import async_session
from app.config import JWT_ALGORITHM, get_settings
from app.models import User
from app.repositories.user_repo import UserRepository
from app.services.cache import check_rate_limit

logger = logging.getLogger(__name__)

settings = get_settings()
security = HTTPBearer(auto_error=False)

redis_client: Redis | None = None


async def get_redis() -> Redis:
    global redis_client
    if redis_client is None:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return redis_client


async def get_db():
    async with async_session() as session:
        yield session


def client_ip(request: Request) -> str:
    """The caller's own address rather than the proxy's, for rate-limit keys.

    Gotcha: both sources are caller-controlled headers, so this is only honest
    while Cloudflare is the sole route to the origin. If the origin ever becomes
    directly reachable, an attacker rotates CF-Connecting-IP and every per-IP
    limit below becomes unenforceable. uvicorn needs --proxy-headers for the
    request.client fallback to be the client and not traefik; see Dockerfile.
    """
    forwarded = request.headers.get("CF-Connecting-IP")
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


class RateLimit(NamedTuple):
    """`limit` requests per `window` seconds per caller, under one Redis bucket."""
    bucket: str
    limit: int
    window: int


async def enforce_rate_limit(rl: RateLimit, request: Request, redis: Redis) -> None:
    allowed, retry_after = await check_rate_limit(
        redis, f"{rl.bucket}:{client_ip(request)}", rl.limit, rl.window
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
        )


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
    """Applied to every route; see the app-level dependency in app/main.py."""
    if request.url.path in UNLIMITED_PATHS:
        return
    try:
        await enforce_rate_limit(GLOBAL_LIMIT, request, redis)
    except RedisError:
        # Fail open. Redis being unreachable is an outage of the limiter, not of
        # the API, and every endpoint behind it already tolerates a cache miss.
        logger.warning("rate limiter unavailable, allowing request", exc_info=True)


def _discord_id_from_token(token: str) -> int | None:
    """The token's subject, or None if the token is unsigned, expired or subjectless."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    discord_id = payload.get("sub")
    return int(discord_id) if discord_id is not None else None


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    discord_id = _discord_id_from_token(credentials.credentials)
    if discord_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await UserRepository(db).get_by_discord_id(discord_id)

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)]
) -> Optional[User]:
    if credentials is None:
        return None

    discord_id = _discord_id_from_token(credentials.credentials)
    if discord_id is None:
        return None

    return await UserRepository(db).get_by_discord_id(discord_id)

