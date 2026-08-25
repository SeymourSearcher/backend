from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.schemas.hex import HexInfoResponse
from app.services.hex_info import get_hex_info

router = APIRouter()


@router.get("/{hex_code}", response_model=HexInfoResponse)
async def hex_info(
    hex_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_dyes: bool = False
):
    return await get_hex_info(db, hex_code, include_dyes)
