from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, insert

from app.dependencies import get_db, get_current_user
from app.schemas import SeymourResponse, UploadResponse
from app.services import parse_seymour_file
from app.models import User, Seymour

router = APIRouter()


@router.get("/", response_model=list[SeymourResponse])
async def list_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500)
):
    result = await db.execute(
        select(Seymour)
        .where(Seymour.user_id == user.id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/upload", response_model=UploadResponse)
async def upload_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...)
):
    content = await file.read()
    text = content.decode("utf-8")
    
    parsed, skipped = parse_seymour_file(text)
    
    # Clear all existing seymours for this user
    await db.execute(delete(Seymour).where(Seymour.user_id == user.id))
    
    # Bulk insert using raw values (much faster for MySQL)
    if parsed:
        values = [
            {"item_name": item.item_name, "hex_color": item.hex_color, "user_id": user.id}
            for item in parsed
        ]
        await db.execute(insert(Seymour), values)
    
    user.last_uploaded = datetime.utcnow()
    await db.commit()
    
    return UploadResponse(imported=len(parsed), skipped=skipped)


@router.get("/{seymour_id}", response_model=SeymourResponse)
async def get_seymour(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Seymour).where(Seymour.id == seymour_id, Seymour.user_id == user.id)
    )
    seymour = result.scalar_one_or_none()
    if not seymour:
        raise HTTPException(status_code=404, detail="Seymour not found")
    return seymour


@router.delete("/{seymour_id}")
async def delete_seymour(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Seymour).where(Seymour.id == seymour_id, Seymour.user_id == user.id)
    )
    seymour = result.scalar_one_or_none()
    if not seymour:
        raise HTTPException(status_code=404, detail="Seymour not found")
    
    await db.delete(seymour)
    await db.commit()
    return {"detail": "Deleted"}


@router.delete("/")
async def delete_all_seymours(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await db.execute(delete(Seymour).where(Seymour.user_id == user.id))
    await db.commit()
    return {"detail": "All seymours deleted"}

