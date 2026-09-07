"""The Discord login flow.

Written alongside the repository refactor: the callback previously built its own
query and raised HTTPException inline, and had no coverage at all, so nothing
would have caught the move to login_with_discord + the DomainError handler.
"""
import httpx
import pytest
from jose import jwt
from sqlalchemy import func, select

from app.config import JWT_ALGORITHM, get_settings
from app.models import User
from app.services import auth

pytestmark = pytest.mark.integration


@pytest.fixture
def discord(monkeypatch):
    """Stand in for Discord; `profile` is what /users/@me returns."""
    state = {"profile": {"id": "42", "username": "tester", "avatar": "abc"}}

    async def exchange_code(code):
        if code == "bad-code":
            raise httpx.HTTPStatusError("bad", request=None, response=None)
        return {"access_token": "discord-token"}

    async def get_discord_user(token):
        if state["profile"] is None:
            raise httpx.HTTPStatusError("bad", request=None, response=None)
        return state["profile"]

    monkeypatch.setattr(auth, "exchange_code", exchange_code)
    monkeypatch.setattr(auth, "get_discord_user", get_discord_user)
    return state


async def test_callback_creates_user_and_returns_usable_jwt(client, db, discord):
    response = await client.post("/auth/discord/callback", json={"code": "good"})
    assert response.status_code == 200

    settings = get_settings()
    payload = jwt.decode(
        response.json()["access_token"], settings.jwt_secret, algorithms=[JWT_ALGORITHM]
    )
    assert payload["sub"] == "42"

    user = (await db.execute(select(User).where(User.discord_id == 42))).scalar_one()
    assert (user.username, user.avatar_hash) == ("tester", "abc")


async def test_callback_refreshes_existing_user_without_duplicating(client, db, discord):
    await client.post("/auth/discord/callback", json={"code": "good"})

    discord["profile"] = {"id": "42", "username": "renamed", "avatar": None}
    response = await client.post("/auth/discord/callback", json={"code": "good"})
    assert response.status_code == 200

    assert await db.scalar(select(func.count()).select_from(User)) == 1
    user = (await db.execute(select(User).where(User.discord_id == 42))).scalar_one()
    assert (user.username, user.avatar_hash) == ("renamed", None)


async def test_bad_code_is_400_and_writes_nothing(client, db, discord):
    response = await client.post("/auth/discord/callback", json={"code": "bad-code"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid code"}
    assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_unreadable_profile_is_400(client, db, discord):
    discord["profile"] = None
    response = await client.post("/auth/discord/callback", json={"code": "good"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Failed to fetch user"}
    assert await db.scalar(select(func.count()).select_from(User)) == 0
