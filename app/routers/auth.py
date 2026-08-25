from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_user
from app.schemas import DiscordCallback, TokenResponse, UserResponse
from app.services.auth import login_with_discord
from app.models import User

router = APIRouter()


@router.post("/discord/callback", response_model=TokenResponse)
async def discord_callback(
    data: DiscordCallback,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    return TokenResponse(access_token=await login_with_discord(db, data.code))


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[User, Depends(get_current_user)]):
    return user
