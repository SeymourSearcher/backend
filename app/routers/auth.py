from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx

from app.dependencies import get_db, get_current_user
from app.schemas import DiscordCallback, TokenResponse, UserResponse
from app.services import exchange_code, get_discord_user, create_jwt
from app.models import User

router = APIRouter()


@router.post("/discord/callback", response_model=TokenResponse)
async def discord_callback(
    data: DiscordCallback,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    try:
        token_data = await exchange_code(data.code)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code")

    discord_token = token_data["access_token"]
    
    try:
        discord_user = await get_discord_user(discord_token)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to fetch user")

    discord_id = int(discord_user["id"])
    username = discord_user["username"]
    avatar = discord_user.get("avatar")

    result = await db.execute(select(User).where(User.discord_id == discord_id))
    user = result.scalar_one_or_none()

    if user:
        user.username = username
        user.avatar_hash = avatar
    else:
        user = User(discord_id=discord_id, username=username, avatar_hash=avatar)
        db.add(user)

    await db.commit()

    jwt_token = create_jwt(discord_id)
    return TokenResponse(access_token=jwt_token)


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[User, Depends(get_current_user)]):
    return user

