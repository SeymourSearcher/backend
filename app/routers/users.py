from typing import Annotated
from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.schemas import UserResponse
from app.models import User

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    user: Annotated[User, Depends(get_current_user)]
):
    return user
