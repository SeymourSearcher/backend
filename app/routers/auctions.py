from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dependencies import get_db
from app.models import ActiveAuction, EndedAuction
from app.models.auction import Bid
from app.schemas.auction import (
    ActiveAuctionResponse,
    ActiveAuctionDetailResponse,
    ActiveAuctionListResponse,
    EndedAuctionResponse,
    EndedAuctionListResponse,
    AuctionStatsResponse,
)
from app.services.auction_queries import (
    AuctionFilters,
    list_auctions,
    parse_pieces,
    serialize,
)
from app.services.auction_stats import compute_auction_stats
from app.services.compute import SETS
from app.services.matching import compute_abs_diff, get_name_for_hex
from app.services.player import get_player_names

router = APIRouter()


async def attach_names(db: AsyncSession, auctions: list, *fields: str) -> None:
    uuids = {getattr(a, f) for a in auctions for f in fields if getattr(a, f)}
    if not uuids:
        return
    names = await get_player_names(db, list(uuids))
    for auction in auctions:
        for field in fields:
            setattr(auction, f"{field.removesuffix('_uuid')}_name", names.get(getattr(auction, field)))


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


@router.get("/recent-bids")
async def list_recent_bids(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=100),
    include_dyes: bool = Query(True),
):
    result = await db.execute(
        select(Bid, ActiveAuction)
        .join(ActiveAuction, Bid.auction_id == ActiveAuction.auction_id)
        .order_by(Bid.timestamp.desc())
        .limit(limit)
    )
    rows = result.all()

    names = await get_player_names(db, list({bid.bidder_uuid for bid, _ in rows}))

    bids = []
    for bid, auction in rows:
        target_hex = (auction.target_hex_all if include_dyes else auction.target_hex_keys) or ""
        delta_e = auction.delta_e_all if include_dyes else auction.delta_e_keys

        bids.append({
            "auction_id": auction.auction_id,
            "bidder_uuid": bid.bidder_uuid,
            "bidder_name": names.get(bid.bidder_uuid),
            "amount": bid.amount,
            "timestamp": bid.timestamp,
            "hex_color": auction.hex_color,
            "piece": auction.piece,
            "target_hex": target_hex,
            "set_name": get_name_for_hex(target_hex),
            "delta_e": round(delta_e, 2) if delta_e is not None else 0.0,
            "abs_diff": compute_abs_diff(auction.hex_color.upper(), target_hex),
        })

    return bids


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
    result = await db.execute(
        select(ActiveAuction)
        .options(selectinload(ActiveAuction.bids))
        .where(ActiveAuction.auction_id == auction_id)
    )
    auction = result.scalar_one_or_none()
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")

    enriched = serialize(auction, ActiveAuctionDetailResponse, include_dyes)
    await attach_names(db, [enriched], "seller_uuid")
    return enriched


@router.get("/ended/{auction_id}", response_model=EndedAuctionResponse)
async def get_ended_auction(
    auction_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_dyes: bool = Query(True),
):
    result = await db.execute(
        select(EndedAuction).where(EndedAuction.auction_id == auction_id)
    )
    auction = result.scalar_one_or_none()
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")

    enriched = serialize(auction, EndedAuctionResponse, include_dyes)
    await attach_names(db, [enriched], "seller_uuid", "buyer_uuid")
    return enriched
