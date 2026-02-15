from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.dependencies import get_db, get_current_user, get_redis
from app.schemas import UserResponse
from app.models import User
from app.services.cache import invalidate_user_seymours

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    user: Annotated[User, Depends(get_current_user)]
):
    return user


@router.delete("/me")
async def delete_account(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    await invalidate_user_seymours(redis, user.id)
    await db.delete(user)
    await db.commit()
    return {"detail": "Account deleted"}

