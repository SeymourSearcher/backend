from typing import Annotated
from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    RateLimit,
    enforce_rate_limit,
    get_current_user,
    get_db,
    get_redis,
)
from app.schemas import DiscordCallback, TokenResponse, UserResponse
from app.services.auth import login_with_discord
from app.models import User

router = APIRouter()

# Each attempt costs two outbound Discord calls, so the limit is there to stop a
# caller burning our OAuth rate budget. Loose enough that a user retrying a
# failed login never trips it.
CALLBACK_LIMIT = RateLimit("auth_callback", 10, 300)


@router.post("/discord/callback", response_model=TokenResponse)
async def discord_callback(
    data: DiscordCallback,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    await enforce_rate_limit(CALLBACK_LIMIT, request, redis)
    return TokenResponse(access_token=await login_with_discord(db, data.code))


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[User, Depends(get_current_user)]):
    return user
