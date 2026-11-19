from fastapi import APIRouter

from app.schemas.catalog import CatalogTarget
from app.services.catalog import target_colours

router = APIRouter()


@router.get("/targets", response_model=list[CatalogTarget])
async def list_targets():
    """Every target colour in the catalog; the frontend's sitemap is built from it."""
    return target_colours()
