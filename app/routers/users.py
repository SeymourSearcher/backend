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

