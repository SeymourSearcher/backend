import logging
from datetime import timedelta

import httpx
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import JWT_ALGORITHM, get_settings
from app.errors import InvalidInputError
from app.repositories.user_repo import UserRepository

settings = get_settings()

logger = logging.getLogger(__name__)

DISCORD_API_URL = "https://discord.com/api/v10"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{DISCORD_API_URL}/oauth2/token",
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not response.is_success:
            logger.warning(
                "Discord OAuth error: %s - %s", response.status_code, response.text
            )
        response.raise_for_status()
        return response.json()


async def get_discord_user(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DISCORD_API_URL}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


def create_jwt(discord_id: int) -> str:
    expire = utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(discord_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


async def login_with_discord(db: AsyncSession, code: str) -> str:
    """Exchange an OAuth code for our own JWT, creating or refreshing the User."""
    try:
        token_data = await exchange_code(code)
    except httpx.HTTPStatusError:
        raise InvalidInputError("Invalid code")

    try:
        discord_user = await get_discord_user(token_data["access_token"])
    except httpx.HTTPStatusError:
        raise InvalidInputError("Failed to fetch user")

    discord_id = int(discord_user["id"])
    username = discord_user["username"]
    avatar = discord_user.get("avatar")

    users = UserRepository(db)
    user = await users.get_by_discord_id(discord_id)
    if user:
        user.username = username
        user.avatar_hash = avatar
    else:
        await users.add(discord_id, username, avatar)

    await db.commit()

    return create_jwt(discord_id)

