from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload

from app.dependencies import get_db
from app.models import ActiveAuction, EndedAuction
from app.schemas.auction import (
    ActiveAuctionResponse,
    ActiveAuctionDetailResponse,
    ActiveAuctionListResponse,
    EndedAuctionResponse,
    EndedAuctionListResponse,
)
from app.services.compute import get_lab, delta_e, find_top_hex_matches, SETS, ITEM_TO_INFO, HEX_TO_ITEMS, ITEM_TO_SET, HEX_TO_DYE_STAGE
from app.services.player import get_player_names

router = APIRouter()

TIER_THRESHOLDS = {"t1": (0, 2), "t2": (2, 5), "t3": (5, 10)}

def get_set_target_hexes(set_name: str) -> list[str]:
    item_names = SETS.get(set_name, [])
    return [ITEM_TO_INFO[name]["hex"] for name in item_names if name in ITEM_TO_INFO]


def get_tier(de: float) -> str | None:
    for tier, (min_de, max_de) in TIER_THRESHOLDS.items():
        if min_de <= de < max_de:
            return tier
    return None


def compute_abs_diff(hex1: str, hex2: str) -> int:
    r1, g1, b1 = int(hex1[0:2], 16), int(hex1[2:4], 16), int(hex1[4:6], 16)
    r2, g2, b2 = int(hex2[0:2], 16), int(hex2[2:4], 16), int(hex2[4:6], 16)
    return abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)


def get_name_for_hex(target_hex: str) -> str | None:
    item_names = HEX_TO_ITEMS.get(target_hex, [])
    if item_names:
        return ITEM_TO_SET.get(item_names[0], item_names[0])
    dye_info = HEX_TO_DYE_STAGE.get(target_hex, [])
    if dye_info:
        return dye_info[0][0]
    return None


def enrich_auction(auction, response_cls, include_dyes: bool = True):
    data = {c: getattr(auction, c) for c in response_cls.model_fields if hasattr(auction, c)}
    hex_color = auction.hex_color.upper()
    matches = find_top_hex_matches(hex_color, limit=1, include_dyes=include_dyes)
    if matches:
        target_hex, de = matches[0]
        data["target_hex"] = target_hex
        data["delta_e"] = round(de, 2)
        data["abs_diff"] = compute_abs_diff(hex_color, target_hex)
        data["tier"] = get_tier(de)
        data["set_name"] = get_name_for_hex(target_hex)
    return response_cls(**data)


def build_filters(model, hex_color: str | None, pieces: list[int] | None, min_price: int | None, max_price: int | None, bin_filter: str | None):
    conditions = []
    if hex_color:
        conditions.append(model.hex_color == hex_color.upper().lstrip("#"))
    if pieces:
        conditions.append(model.piece.in_(pieces))
    if min_price is not None:
        conditions.append(model.price >= min_price)
    if max_price is not None:
        conditions.append(model.price <= max_price)
    if bin_filter == "bin":
        conditions.append(model.is_bin == True)
    elif bin_filter == "nobin":
        conditions.append(model.is_bin == False)
    return and_(*conditions) if conditions else True


def filter_by_tier_and_set(auctions: list, tier: str | None, set_name: str | None) -> list:
    if not tier and not set_name:
        return auctions
    
    target_hexes = set(get_set_target_hexes(set_name)) if set_name else None
    tier_range = TIER_THRESHOLDS.get(tier) if tier else None
    
    filtered = []
    for a in auctions:
        if target_hexes and a.target_hex not in target_hexes:
            continue
        if tier_range:
            if not (tier_range[0] <= a.delta_e < tier_range[1]):
                continue
        filtered.append(a)
    return filtered


def sort_auctions(auctions: list, sort_by: str, sort_order: str, is_ended: bool = False):
    reverse = sort_order == "desc"
    if sort_by == "price":
        return sorted(auctions, key=lambda a: a.price, reverse=reverse)
    elif sort_by == "timeleft" and not is_ended:
        return sorted(auctions, key=lambda a: a.end_time or 0, reverse=reverse)
    elif sort_by == "timestamp" and is_ended:
        return sorted(auctions, key=lambda a: a.timestamp, reverse=reverse)
    return auctions


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
    piece_list = [int(p) for p in pieces.split(",") if p.isdigit()] if pieces else None
    filters = build_filters(ActiveAuction, hex_color, piece_list, min_price, max_price, bin_filter)
    
    result = await db.execute(select(ActiveAuction).where(filters))
    auctions = result.scalars().all()
    enriched = [enrich_auction(a, ActiveAuctionResponse, include_dyes) for a in auctions]
    filtered = filter_by_tier_and_set(enriched, tier, set_name)
    sorted_auctions = sort_auctions(filtered, sort_by, sort_order)
    
    total = len(sorted_auctions)
    paginated = sorted_auctions[skip:skip + limit]
    
    seller_uuids = list({a.seller_uuid for a in paginated})
    names = await get_player_names(db, seller_uuids)
    for auction in paginated:
        auction.seller_name = names.get(auction.seller_uuid)
    
    return ActiveAuctionListResponse(auctions=paginated, total=total)


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
    piece_list = [int(p) for p in pieces.split(",") if p.isdigit()] if pieces else None
    filters = build_filters(EndedAuction, hex_color, piece_list, min_price, max_price, bin_filter)
    
    result = await db.execute(select(EndedAuction).where(filters))
    auctions = result.scalars().all()
    enriched = [enrich_auction(a, EndedAuctionResponse, include_dyes) for a in auctions]
    filtered = filter_by_tier_and_set(enriched, tier, set_name)
    sorted_auctions = sort_auctions(filtered, sort_by, sort_order, is_ended=True)
    
    total = len(sorted_auctions)
    paginated = sorted_auctions[skip:skip + limit]
    
    all_uuids = list({a.seller_uuid for a in paginated} | {a.buyer_uuid for a in paginated})
    names = await get_player_names(db, all_uuids)
    for auction in paginated:
        auction.seller_name = names.get(auction.seller_uuid)
        auction.buyer_name = names.get(auction.buyer_uuid)
    
    return EndedAuctionListResponse(auctions=paginated, total=total)


@router.get("/recent-bids")
async def list_recent_bids(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=100),
    include_dyes: bool = Query(True),
):
    from app.models.auction import Bid
    
    result = await db.execute(
        select(Bid, ActiveAuction)
        .join(ActiveAuction, Bid.auction_id == ActiveAuction.auction_id)
        .order_by(Bid.timestamp.desc())
        .limit(limit)
    )
    rows = result.all()
    
    bidder_uuids = list({row[0].bidder_uuid for row in rows})
    names = await get_player_names(db, bidder_uuids)
    
    bids = []
    for bid, auction in rows:
        matches = find_top_hex_matches(auction.hex_color.upper(), limit=1, include_dyes=include_dyes)
        target_hex = matches[0][0] if matches else ""
        de = round(matches[0][1], 2) if matches else 0.0
        set_name = get_name_for_hex(target_hex) if target_hex else None
        
        abs_diff = compute_abs_diff(auction.hex_color.upper(), target_hex) if target_hex else 0
        
        bids.append({
            "auction_id": auction.auction_id,
            "bidder_uuid": bid.bidder_uuid,
            "bidder_name": names.get(bid.bidder_uuid),
            "amount": bid.amount,
            "timestamp": bid.timestamp,
            "hex_color": auction.hex_color,
            "piece": auction.piece,
            "target_hex": target_hex,
            "set_name": set_name,
            "delta_e": de,
            "abs_diff": abs_diff,
        })
    
    return bids


@router.get("/sets", response_model=list[str])
async def list_available_sets():
    return sorted(SETS.keys())


@router.get("/active/{auction_id}", response_model=ActiveAuctionDetailResponse)
async def get_active_auction(
    auction_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(ActiveAuction)
        .options(selectinload(ActiveAuction.bids))
        .where(ActiveAuction.auction_id == auction_id)
    )
    auction = result.scalar_one_or_none()
    
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    
    enriched = enrich_auction(auction, ActiveAuctionDetailResponse)
    names = await get_player_names(db, [auction.seller_uuid])
    enriched.seller_name = names.get(auction.seller_uuid)
    return enriched


@router.get("/ended/{auction_id}", response_model=EndedAuctionResponse)
async def get_ended_auction(
    auction_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(EndedAuction).where(EndedAuction.auction_id == auction_id)
    )
    auction = result.scalar_one_or_none()
    
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    
    enriched = enrich_auction(auction, EndedAuctionResponse)
    names = await get_player_names(db, [auction.seller_uuid, auction.buyer_uuid])
    enriched.seller_name = names.get(auction.seller_uuid)
    enriched.buyer_name = names.get(auction.buyer_uuid)
    return enriched
