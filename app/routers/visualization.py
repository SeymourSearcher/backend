import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from redis.asyncio import Redis

from app.dependencies import get_redis
from app.schemas.visualization import VisualizationRequest, VisualizationStats
from app.services.cache import check_rate_limit
from app.services.visualization import generate_color_mask, get_stats, generate_3d_html

router = APIRouter()

HEX_GENERATE_RATE_LIMIT = 2
HEX_GENERATE_WINDOW = 300

HEX_STATS_RATE_LIMIT = 20
HEX_STATS_WINDOW = 60


async def _enforce_rate_limit(
    req: VisualizationRequest, request: Request, redis: Redis,
    bucket: str, limit: int, window: int,
) -> None:
    """Limits every request that is not scoped to the caller's own Seymours.

    Without input_seymours the query scans all 16.7M colours, so the unscoped
    case is the expensive one -- it previously went unlimited because the check
    only fired when input_hex_codes was set.
    """
    if req.input_seymours:
        return

    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = await check_rate_limit(
        redis, f"{bucket}:{client_ip}", limit, window
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {retry_after} seconds."
        )


async def _color_mask(req: VisualizationRequest):
    return await asyncio.to_thread(
        generate_color_mask,
        min_de=req.min_de,
        max_de=req.max_de,
        input_hex_codes=req.input_hex_codes,
        input_seymours=req.input_seymours,
        include_dyes=req.include_dyes,
    )


@router.post("/stats", response_model=VisualizationStats)
async def get_visualization_stats(
    req: VisualizationRequest,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)]
):
    await _enforce_rate_limit(
        req, request, redis, "viz_stats", HEX_STATS_RATE_LIMIT, HEX_STATS_WINDOW
    )
    indices, total = await _color_mask(req)
    return get_stats(len(indices), total)


@router.post("/generate", response_class=HTMLResponse)
async def generate_visualization(
    req: VisualizationRequest,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)]
):
    await _enforce_rate_limit(
        req, request, redis, "viz_hex", HEX_GENERATE_RATE_LIMIT, HEX_GENERATE_WINDOW
    )
    indices, total = await _color_mask(req)
    return await asyncio.to_thread(generate_3d_html, indices, total, req.max_points)
