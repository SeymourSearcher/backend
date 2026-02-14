from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_user
from app.schemas import UserResponse
from app.models import User

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    user: Annotated[User, Depends(get_current_user)]
):
    return user


@router.delete("/me")
async def delete_account(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await db.delete(user)
    await db.commit()
    return {"detail": "Account deleted"}

