from typing import Annotated, Optional
from fastapi import Depends, HTTPException, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
from redis.asyncio import Redis

from app.database import async_session
from app.config import JWT_ALGORITHM, get_settings
from app.models import User
from app.repositories.user_repo import UserRepository

settings = get_settings()
security = HTTPBearer(auto_error=False)

redis_client: Redis | None = None


async def get_redis() -> Redis:
    global redis_client
    if redis_client is None:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return redis_client


PUBLIC_MAX_AGE = 60
CATALOG_MAX_AGE = 86400


def public_cache(max_age: int = PUBLIC_MAX_AGE):
    """Route dependency marking a success response cacheable by browsers and CDNs.

    Only for responses identical for every caller: never on anything that
    reads the user. Error responses don't carry it.
    """
    def set_header(response: Response) -> None:
        response.headers["Cache-Control"] = f"public, max-age={max_age}"

    return Depends(set_header)


async def get_db():
    async with async_session() as session:
        yield session


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

