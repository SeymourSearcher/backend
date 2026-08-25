from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_user
from app.models import User, OrderStatus, OrderType, Tier
from app.pieces import Piece
from app.schemas.common import DetailResponse
from app.schemas.marketplace import (
    BuyOrderCreate,
    BuyOrderResponse,
    BuyOrderListResponse,
    FillableOrderResponse,
    SellOrderCreate,
    SellOrderResponse,
    SellOrderListResponse,
)
from app.services import buy_orders, sell_orders
from app.services.buy_orders import BuyOrderFilters
from app.services.marketplace import get_all_sets_pieces
from app.services.sell_orders import SellOrderFilters

router = APIRouter()


@router.get("/sets-pieces", response_model=dict[str, list[Piece]])
async def get_sets_pieces():
    return get_all_sets_pieces()


@router.post("/orders", response_model=BuyOrderResponse)
async def create_order(
    data: BuyOrderCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    return await buy_orders.create(db, user, data)


@router.get("/orders", response_model=BuyOrderListResponse)
async def list_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    order_type: OrderType | None = None,
    set_name: str | None = None,
    piece_types: str | None = Query(None, description="Comma-separated piece types (0-3)"),
    tier: Tier | None = None,
    sort_by: Literal["price", "expires_at"] = "price",
    sort_order: Literal["asc", "desc"] = "desc",
):
    filters = BuyOrderFilters(
        order_type=order_type,
        set_name=set_name,
        piece_types=[int(p.strip()) for p in piece_types.split(",") if p.strip().isdigit()]
        if piece_types
        else None,
        tier=tier,
    )
    orders, total = await buy_orders.list_active(db, filters, sort_by, sort_order, skip, limit)
    return BuyOrderListResponse(orders=orders, total=total)


@router.get("/orders/mine", response_model=BuyOrderListResponse)
async def list_my_orders(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: OrderStatus | None = None,
    sort_by: Literal["price", "expires_at"] = "price",
    sort_order: Literal["asc", "desc"] = "desc",
):
    orders, total = await buy_orders.list_for_user(
        db, user, status, sort_by, sort_order, skip, limit
    )
    return BuyOrderListResponse(orders=orders, total=total)


@router.get("/orders/matching/{seymour_id}", response_model=BuyOrderListResponse)
async def find_matching_orders(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    orders, total = await buy_orders.list_matching(db, user, seymour_id, skip, limit)
    return BuyOrderListResponse(orders=orders, total=total)


@router.get("/orders/fillable", response_model=list[FillableOrderResponse])
async def list_fillable_orders(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: Literal["price", "expires_at"] = "price",
    sort_order: Literal["asc", "desc"] = "desc",
):
    return await buy_orders.list_fillable(db, user, sort_by, sort_order, skip, limit)


@router.post("/orders/sell", response_model=list[SellOrderResponse])
async def create_sell_orders(
    data: SellOrderCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    return await sell_orders.create(db, user, data)


@router.get("/orders/sell", response_model=SellOrderListResponse)
async def list_sell_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    piece_type: int | None = Query(None, ge=0, le=3),
    hex_color: str | None = Query(None, min_length=6, max_length=6),
    min_price: int | None = Query(None, ge=0),
    max_price: int | None = Query(None, ge=0),
    sort_by: Literal["price", "expires_at", "delta_e"] = "price",
    sort_order: Literal["asc", "desc"] = "asc",
):
    filters = SellOrderFilters(
        piece_type=piece_type,
        hex_color=hex_color,
        min_price=min_price,
        max_price=max_price,
    )
    orders, total = await sell_orders.list_active(db, filters, sort_by, sort_order, skip, limit)
    return SellOrderListResponse(orders=orders, total=total)


@router.get("/orders/sell/mine", response_model=SellOrderListResponse)
async def list_my_sell_orders(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: OrderStatus | None = None,
    sort_by: Literal["price", "expires_at"] = "price",
    sort_order: Literal["asc", "desc"] = "desc",
):
    orders, total = await sell_orders.list_for_user(
        db, user, status, sort_by, sort_order, skip, limit
    )
    return SellOrderListResponse(orders=orders, total=total)


@router.delete("/orders/sell/{order_id}", response_model=DetailResponse)
async def cancel_sell_order(
    order_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await sell_orders.cancel(db, user, order_id)
    return {"detail": "Sell order cancelled"}


@router.get("/orders/{order_id}", response_model=BuyOrderResponse)
async def get_order(
    order_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    return await buy_orders.get(db, order_id)


@router.delete("/orders/{order_id}", response_model=DetailResponse)
async def cancel_order(
    order_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await buy_orders.cancel(db, user, order_id)
    return {"detail": "Order cancelled"}
