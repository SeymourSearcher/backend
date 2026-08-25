from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import ActiveAuction, EndedAuction
from app.schemas.auction import (
    ActiveAuctionResponse,
    ActiveAuctionDetailResponse,
    ActiveAuctionListResponse,
    EndedAuctionResponse,
    EndedAuctionListResponse,
    AuctionStatsResponse,
    RecentBidResponse,
)
from app.services.auction_queries import (
    AuctionFilters,
    attach_names,
    get_auction,
    list_auctions,
    parse_pieces,
    recent_bids,
)
from app.services.auction_stats import compute_auction_stats
from app.services.compute import SETS

router = APIRouter()


@router.get("/active", response_model=ActiveAuctionListResponse)
async def list_active_auctions(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    hex_color: str | None = Query(None),
    pieces: str | None = Query(None),
    min_price: int | None = Query(None, ge=0),
    max_price: int | None = Query(None, ge=0),
    bin_filter: Literal["all", "bin", "nobin"] | None = Query(None),
    tier: Literal["t1", "t2", "t3"] | None = Query(None),
    set_name: str | None = Query(None),
    sort_by: Literal["price", "timeleft"] = Query("timeleft"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
    include_dyes: bool = Query(True),
):
    filters = AuctionFilters(
        hex_color=hex_color,
        pieces=parse_pieces(pieces),
        min_price=min_price,
        max_price=max_price,
        bin_filter=bin_filter,
        tier=tier,
        set_name=set_name,
        include_dyes=include_dyes,
    )
    auctions, total = await list_auctions(
        db, ActiveAuction, ActiveAuctionResponse, filters, sort_by, sort_order, skip, limit
    )
    await attach_names(db, auctions, "seller_uuid")
    return ActiveAuctionListResponse(auctions=auctions, total=total)


@router.get("/ended", response_model=EndedAuctionListResponse)
async def list_ended_auctions(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    hex_color: str | None = Query(None),
    pieces: str | None = Query(None),
    min_price: int | None = Query(None, ge=0),
    max_price: int | None = Query(None, ge=0),
    bin_filter: Literal["all", "bin", "nobin"] | None = Query(None),
    tier: Literal["t1", "t2", "t3"] | None = Query(None),
    set_name: str | None = Query(None),
    sort_by: Literal["price", "timestamp"] = Query("timestamp"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    include_dyes: bool = Query(True),
):
    filters = AuctionFilters(
        hex_color=hex_color,
        pieces=parse_pieces(pieces),
        min_price=min_price,
        max_price=max_price,
        bin_filter=bin_filter,
        tier=tier,
        set_name=set_name,
        include_dyes=include_dyes,
    )
    auctions, total = await list_auctions(
        db, EndedAuction, EndedAuctionResponse, filters, sort_by, sort_order, skip, limit
    )
    await attach_names(db, auctions, "seller_uuid", "buyer_uuid")
    return EndedAuctionListResponse(auctions=auctions, total=total)


@router.get("/recent-bids", response_model=list[RecentBidResponse])
async def list_recent_bids(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=100),
    include_dyes: bool = Query(True),
):
    return await recent_bids(db, limit, include_dyes)


@router.get("/sets", response_model=list[str])
async def list_available_sets():
    return sorted(SETS.keys())


@router.get("/stats", response_model=AuctionStatsResponse)
async def get_auction_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    hex_color: str | None = Query(None),
    pieces: str | None = Query(None),
    min_price: int | None = Query(None, ge=0),
    max_price: int | None = Query(None, ge=0),
    bin_filter: Literal["all", "bin", "nobin"] | None = Query(None),
    tier: Literal["t1", "t2", "t3"] | None = Query(None),
    set_name: str | None = Query(None),
    include_dyes: bool = Query(True),
):
    filters = AuctionFilters(
        hex_color=hex_color,
        pieces=parse_pieces(pieces),
        min_price=min_price,
        max_price=max_price,
        bin_filter=bin_filter,
        tier=tier,
        set_name=set_name,
        include_dyes=include_dyes,
    )
    return AuctionStatsResponse(**await compute_auction_stats(db, filters))


@router.get("/active/{auction_id}", response_model=ActiveAuctionDetailResponse)
async def get_active_auction(
    auction_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_dyes: bool = Query(True),
):
    auction = await get_auction(
        db, ActiveAuction, auction_id, ActiveAuctionDetailResponse, include_dyes
    )
    await attach_names(db, [auction], "seller_uuid")
    return auction


@router.get("/ended/{auction_id}", response_model=EndedAuctionResponse)
async def get_ended_auction(
    auction_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_dyes: bool = Query(True),
):
    auction = await get_auction(
        db, EndedAuction, auction_id, EndedAuctionResponse, include_dyes
    )
    await attach_names(db, [auction], "seller_uuid", "buyer_uuid")
    return auction
