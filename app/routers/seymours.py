from typing import Annotated, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.dependencies import get_db, get_current_user, get_redis, get_optional_user
from app.pieces import Piece
from app.schemas.common import DetailResponse
from app.schemas import (
    RemovalResponse,
    SeymourResponse,
    SeymourWithMatchResponse,
    SubFilterOption,
    UploadResponse,
)
from app.services import seymours
from app.models import User
from app.rate_limit import RateLimit, user_limiter

# Upload and delete each rebuild the owner-hex table and clear every hex-page
# cache, so one account looping them keeps the database busy for everyone.
COLLECTION_WRITE_LIMIT = RateLimit("collection_write", 10, 60)
_collection_write = [Depends(user_limiter(COLLECTION_WRITE_LIMIT))]

router = APIRouter()


@router.get("/available-filters", response_model=list[str])
async def get_available_filters(
    user: Annotated[Optional[User], Depends(get_optional_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    filter: str = Query(...),
    incomplete_only: bool = Query(False),
    include_dyes: bool = Query(False)
):
    return await seymours.available_filters(
        db, redis, user, filter, incomplete_only, include_dyes
    )


@router.get("/sub-filters", response_model=list[SubFilterOption])
async def get_owned_sub_filters(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    filter: str = Query(...),
    incomplete_only: bool = Query(False),
    include_dyes: bool = Query(False),
):
    """The /items dropdown. Distinct from available-filters, which the
    marketplace needs unrestricted -- see services/seymour_filters.py."""
    return await seymours.owned_sub_filters(
        db, redis, user, filter, incomplete_only, include_dyes
    )


@router.get("/hex-codes", response_model=list[str])
async def get_user_hex_codes(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await seymours.hex_codes(db, redis, user)


@router.get("/hex-codes/with-pieces", response_model=list[tuple[str, Piece]])
async def get_user_hex_codes_with_pieces(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await seymours.hex_codes_with_pieces(db, redis, user)


@router.get("/", response_model=list[SeymourWithMatchResponse])
async def list_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    filter: Optional[str] = Query(None),
    sub_filter: Optional[str] = Query(None),
    incomplete_only: bool = Query(False),
    include_dyes: bool = Query(False),
    sort: Optional[str] = Query(None)
):
    filter_type = filter if filter and filter != "all" else "all"
    return await seymours.list_for_user(
        db, redis, user, filter_type, sub_filter, incomplete_only, include_dyes, sort, skip, limit
    )


@router.post("/upload", response_model=UploadResponse, dependencies=_collection_write)
async def upload_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    file: UploadFile = File(...)
):
    content = await seymours.read_upload(file)
    imported, skipped = await seymours.replace_all(db, redis, user, content)
    return UploadResponse(imported=imported, skipped=skipped)


@router.get("/{seymour_id}", response_model=SeymourResponse)
async def get_seymour(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    return await seymours.get_owned(db, user, seymour_id)


@router.delete("/{seymour_id}", response_model=DetailResponse, dependencies=_collection_write)
async def delete_seymour(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    await seymours.delete_one(db, redis, user, seymour_id)
    return {"detail": "Deleted"}


@router.delete("/", response_model=RemovalResponse, dependencies=_collection_write)
async def delete_all_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    return await seymours.delete_all(db, redis, user)
