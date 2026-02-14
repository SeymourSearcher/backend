from datetime import datetime, timedelta
import httpx
from jose import jwt

from app.config import get_settings

settings = get_settings()

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
            print(f"Discord OAuth error: {response.status_code} - {response.text}")
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
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(discord_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

