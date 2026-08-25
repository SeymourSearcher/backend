from typing import Annotated, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.dependencies import get_db, get_current_user, get_redis, get_optional_user
from app.pieces import Piece
from app.schemas.common import DetailResponse
from app.schemas import SeymourResponse, UploadResponse, SeymourWithMatchResponse
from app.services import seymours
from app.models import User

router = APIRouter()


@router.get("/available-filters", response_model=list[str])
async def get_available_filters(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    filter: str = Query(...),
    incomplete_only: bool = Query(False),
    include_dyes: bool = Query(False)
):
    user = await get_optional_user(request, db)
    return await seymours.available_filters(
        db, redis, user, filter, incomplete_only, include_dyes
    )


@router.get("/hex-codes", response_model=list[str] | list[tuple[str, Piece]])
async def get_user_hex_codes(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    with_pieces: bool = Query(False)
):
    return await seymours.hex_codes(db, redis, user, with_pieces)


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


@router.post("/upload", response_model=UploadResponse)
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


@router.delete("/{seymour_id}", response_model=DetailResponse)
async def delete_seymour(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    await seymours.delete_one(db, redis, user, seymour_id)
    return {"detail": "Deleted"}


@router.delete("/", response_model=DetailResponse)
async def delete_all_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)]
):
    await seymours.delete_all(db, redis, user)
    return {"detail": "All seymours deleted"}
