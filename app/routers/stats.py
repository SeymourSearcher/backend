from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_user
from app.schemas.stats import (
    FeaturedSetResponse,
    RankingResponse,
    RankingSort,
    StatsResponse,
)
from app.services import custom_sets as custom_sets_service
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
async def get_rankings(
    db: Annotated[AsyncSession, Depends(get_db)],
    sort_by: RankingSort = RankingSort.total_seymours,
):
    return await rankings_service.rankings(db, sort_by.value)


@router.get("/custom-sets", response_model=list[FeaturedSetResponse])
async def get_featured_sets(db: Annotated[AsyncSession, Depends(get_db)]):
    return await custom_sets_service.featured_sets(db)
