from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_user
from app.schemas.stats import RankingResponse, StatsResponse
from app.services import rankings as rankings_service
from app.models import User

router = APIRouter()


@router.get("/", response_model=StatsResponse)
async def get_global_stats(db: Annotated[AsyncSession, Depends(get_db)]):
    return await rankings_service.global_stats(db)


@router.get("/me", response_model=StatsResponse)
async def get_user_stats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await rankings_service.user_stats(db, user)


@router.get("/rankings", response_model=list[RankingResponse])
async def get_rankings(db: Annotated[AsyncSession, Depends(get_db)]):
    return await rankings_service.rankings(db)
