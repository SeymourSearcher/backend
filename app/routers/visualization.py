from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from redis.asyncio import Redis

from app.dependencies import get_redis
from app.services.cache import check_rate_limit
from app.services.visualization import generate_color_mask, get_stats, generate_3d_html

router = APIRouter()

HEX_GENERATE_RATE_LIMIT = 2
HEX_GENERATE_WINDOW = 300


class VisualizationRequest(BaseModel):
    min_de: float = 0.0
    max_de: float = 5.0
    input_hex_codes: list[str] | None = None
    input_seymours: list[tuple[str, int]] | None = None
    include_dyes: bool = True
    max_points: int = 100000


class VisualizationStats(BaseModel):
    count: int
    total: int
    percentage: float


@router.post("/stats", response_model=VisualizationStats)
async def get_visualization_stats(req: VisualizationRequest):
    indices, total = generate_color_mask(
        min_de=req.min_de,
        max_de=req.max_de,
        input_hex_codes=req.input_hex_codes,
        input_seymours=req.input_seymours,
        include_dyes=req.include_dyes
    )
    return get_stats(len(indices), total)


@router.post("/generate", response_class=HTMLResponse)
async def generate_visualization(
    req: VisualizationRequest,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)]
):
    if req.input_hex_codes and not req.input_seymours:
        client_ip = request.client.host if request.client else "unknown"
        allowed, retry_after = await check_rate_limit(
            redis, f"viz_hex:{client_ip}", HEX_GENERATE_RATE_LIMIT, HEX_GENERATE_WINDOW
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds."
            )

    indices, total = generate_color_mask(
        min_de=req.min_de,
        max_de=req.max_de,
        input_hex_codes=req.input_hex_codes,
        input_seymours=req.input_seymours,
        include_dyes=req.include_dyes
    )
    return generate_3d_html(indices, total, max_points=req.max_points)
