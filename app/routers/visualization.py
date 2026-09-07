import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from redis.asyncio import Redis

from app.dependencies import get_redis
from app.rate_limit import RateLimit, enforce
from app.schemas.visualization import VisualizationRequest, VisualizationStats
from app.services.color_index import ColorSelection
from app.services.visualization import select_colors, get_stats, generate_3d_html

router = APIRouter()

# Both branches are limited; the unscoped one harder. It selects from all 16.7M
# colours, so it returns a full-size point cloud however small the caller's
# collection is -- the largest response the API serves. Its delta E scan is no
# longer what costs, see app/services/color_index.py, but the payload still is.
# The scoped branch is bounded by MAX_INPUTS, so it only needs a flood guard.
GENERATE_UNSCOPED = RateLimit("viz_hex", 2, 300)
GENERATE_SCOPED = RateLimit("viz_hex:scoped", 20, 300)

STATS_UNSCOPED = RateLimit("viz_stats", 20, 60)
STATS_SCOPED = RateLimit("viz_stats:scoped", 60, 60)


async def _enforce(
    req: VisualizationRequest, request: Request, redis: Redis,
    unscoped: RateLimit, scoped: RateLimit,
) -> None:
    """Not a router-level limiter(): which bucket applies depends on the body."""
    await enforce(scoped if req.input_seymours else unscoped, request, redis)


async def _select(req: VisualizationRequest) -> ColorSelection:
    return await asyncio.to_thread(
        select_colors,
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
    await _enforce(req, request, redis, STATS_UNSCOPED, STATS_SCOPED)
    selection = await _select(req)
    return get_stats(selection.count, selection.total)


@router.post("/generate", response_class=HTMLResponse)
async def generate_visualization(
    req: VisualizationRequest,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)]
):
    await _enforce(req, request, redis, GENERATE_UNSCOPED, GENERATE_SCOPED)
    selection = await _select(req)
    return await asyncio.to_thread(generate_3d_html, selection, req.max_points)
