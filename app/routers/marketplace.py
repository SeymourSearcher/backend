from datetime import datetime
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, cast, String, asc, desc
from sqlalchemy.orm import selectinload

from app.dependencies import get_db, get_current_user
from app.models import User, Seymour, BuyOrder, BuyOrderSetCriteria, BuyOrderHexCriteria, SellOrder, OrderType, OrderStatus, Tier
from app.schemas.marketplace import (
    BuyOrderCreate,
    BuyOrderResponse,
    BuyOrderListResponse,
    FillableOrderResponse,
    SellOrderCreate,
    SellOrderResponse,
    SellOrderListResponse,
)
from app.services.marketplace import (
    is_valid_set,
    is_valid_piece_for_set,
    seymour_matches_set_order,
    seymour_matches_hex_order,
    get_all_sets_pieces,
    get_seymour_best_match,
)

router = APIRouter()


@router.get("/sets-pieces")
async def get_sets_pieces():
    return get_all_sets_pieces()


def expire_if_needed(order: BuyOrder) -> BuyOrder:
    if order.status == OrderStatus.ACTIVE and order.expires_at < datetime.utcnow():
        order.status = OrderStatus.EXPIRED
    return order


@router.post("/orders", response_model=BuyOrderResponse)
async def create_order(
    data: BuyOrderCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    if data.order_type == OrderType.SET_PIECE:
        if not data.set_criteria:
            raise HTTPException(400, "set_criteria required for SET_PIECE orders")
        if not is_valid_set(data.set_criteria.set_name):
            raise HTTPException(400, f"Invalid set name: {data.set_criteria.set_name}")
        for pt in data.set_criteria.piece_types:
            if not is_valid_piece_for_set(data.set_criteria.set_name, pt):
                raise HTTPException(400, f"Piece type {pt} is not available for this set")
    elif data.order_type == OrderType.HEX_PATTERN:
        if not data.hex_criteria:
            raise HTTPException(400, "hex_criteria required for HEX_PATTERN orders")

    order = BuyOrder(
        user_id=user.id,
        price=data.price,
        order_type=data.order_type,
    )
    db.add(order)
    await db.flush()

    if data.set_criteria:
        criteria = BuyOrderSetCriteria(
            order_id=order.id,
            set_name=data.set_criteria.set_name,
            piece_types=data.set_criteria.piece_types,
            tier=data.set_criteria.tier,
        )
        db.add(criteria)

    if data.hex_criteria:
        criteria = BuyOrderHexCriteria(
            order_id=order.id,
            match_type=data.hex_criteria.match_type,
            hex_value=data.hex_criteria.hex_value,
            max_delta_e=data.hex_criteria.max_delta_e,
            piece_type=data.hex_criteria.piece_type,
        )
        db.add(criteria)

    await db.commit()
    await db.refresh(order, ["set_criteria", "hex_criteria"])
    return order


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
    query = (
        select(BuyOrder)
        .options(
            selectinload(BuyOrder.set_criteria),
            selectinload(BuyOrder.hex_criteria),
            selectinload(BuyOrder.user),
        )
        .where(BuyOrder.status == OrderStatus.ACTIVE)
        .where(BuyOrder.expires_at > datetime.utcnow())
    )

    if order_type:
        query = query.where(BuyOrder.order_type == order_type)

    joined_set_criteria = False
    if set_name:
        query = query.join(BuyOrderSetCriteria).where(BuyOrderSetCriteria.set_name == set_name)
        joined_set_criteria = True

    if tier:
        if not joined_set_criteria:
            query = query.join(BuyOrderSetCriteria)
            joined_set_criteria = True
        query = query.where(BuyOrderSetCriteria.tier == tier)

    if piece_types:
        piece_list = [int(p.strip()) for p in piece_types.split(",") if p.strip().isdigit()]
        if piece_list:
            if not joined_set_criteria:
                query = query.outerjoin(BuyOrderSetCriteria, BuyOrder.id == BuyOrderSetCriteria.order_id, full=False)
            query = query.outerjoin(BuyOrderHexCriteria, BuyOrder.id == BuyOrderHexCriteria.order_id, full=False)
            query = query.where(
                or_(
                    *[func.instr(cast(BuyOrderSetCriteria.piece_types, String), str(pt)) > 0 for pt in piece_list],
                    BuyOrderHexCriteria.piece_type.in_(piece_list)
                )
            )

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    sort_col = BuyOrder.price if sort_by == "price" else BuyOrder.expires_at
    order_func = desc if sort_order == "desc" else asc
    query = query.order_by(order_func(sort_col)).offset(skip).limit(limit)
    result = await db.execute(query)
    orders = list(result.scalars().all())

    for order in orders:
        expire_if_needed(order)

    await db.commit()
    active_orders = [o for o in orders if o.status == OrderStatus.ACTIVE]

    return BuyOrderListResponse(orders=active_orders, total=total or 0)


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
    query = (
        select(BuyOrder)
        .options(selectinload(BuyOrder.set_criteria), selectinload(BuyOrder.hex_criteria))
        .where(BuyOrder.user_id == user.id)
    )

    if status:
        query = query.where(BuyOrder.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    sort_col = BuyOrder.price if sort_by == "price" else BuyOrder.expires_at
    order_func = desc if sort_order == "desc" else asc
    query = query.order_by(order_func(sort_col)).offset(skip).limit(limit)
    result = await db.execute(query)
    orders = list(result.scalars().all())

    for order in orders:
        expire_if_needed(order)

    await db.commit()

    return BuyOrderListResponse(orders=orders, total=total or 0)


@router.get("/orders/matching/{seymour_id}", response_model=BuyOrderListResponse)
async def find_matching_orders(
    seymour_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    seymour_result = await db.execute(
        select(Seymour).where(Seymour.id == seymour_id, Seymour.user_id == user.id)
    )
    seymour = seymour_result.scalar_one_or_none()
    if not seymour:
        raise HTTPException(404, "Seymour not found")

    result = await db.execute(
        select(BuyOrder)
        .options(selectinload(BuyOrder.set_criteria), selectinload(BuyOrder.hex_criteria))
        .where(BuyOrder.status == OrderStatus.ACTIVE)
        .where(BuyOrder.expires_at > datetime.utcnow())
        .where(BuyOrder.user_id != user.id)
    )
    orders = list(result.scalars().all())

    matching = []
    for order in orders:
        expire_if_needed(order)
        if order.status != OrderStatus.ACTIVE:
            continue

        if order.order_type == OrderType.SET_PIECE and order.set_criteria:
            if seymour_matches_set_order(
                seymour.hex_color,
                seymour.item_name,
                order.set_criteria.set_name,
                order.set_criteria.piece_types,
                order.set_criteria.tier.value,
            ):
                matching.append(order)

        elif order.order_type == OrderType.HEX_PATTERN and order.hex_criteria:
            if seymour_matches_hex_order(
                seymour.hex_color,
                seymour.item_name,
                order.hex_criteria.match_type.value,
                order.hex_criteria.hex_value,
                order.hex_criteria.max_delta_e,
                order.hex_criteria.piece_type,
            ):
                matching.append(order)

    await db.commit()

    matching.sort(key=lambda o: o.price, reverse=True)
    total = len(matching)
    paginated = matching[skip:skip + limit]

    return BuyOrderListResponse(orders=paginated, total=total)


@router.get("/orders/fillable", response_model=list[FillableOrderResponse])
async def list_fillable_orders(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: Literal["price", "expires_at"] = "price",
    sort_order: Literal["asc", "desc"] = "desc",
):
    seymours_result = await db.execute(
        select(Seymour).where(Seymour.user_id == user.id)
    )
    user_seymours = list(seymours_result.scalars().all())

    if not user_seymours:
        return []

    orders_result = await db.execute(
        select(BuyOrder)
        .options(
            selectinload(BuyOrder.set_criteria),
            selectinload(BuyOrder.hex_criteria),
            selectinload(BuyOrder.user),
        )
        .where(BuyOrder.status == OrderStatus.ACTIVE)
        .where(BuyOrder.expires_at > datetime.utcnow())
        .where(BuyOrder.user_id != user.id)
    )
    orders = list(orders_result.scalars().all())

    fillable = []
    for order in orders:
        expire_if_needed(order)
        if order.status != OrderStatus.ACTIVE:
            continue

        matching_seymours = []
        for seymour in user_seymours:
            is_match = False
            if order.order_type == OrderType.SET_PIECE and order.set_criteria:
                is_match = seymour_matches_set_order(
                    seymour.hex_color,
                    seymour.item_name,
                    order.set_criteria.set_name,
                    order.set_criteria.piece_types,
                    order.set_criteria.tier.value,
                )
            elif order.order_type == OrderType.HEX_PATTERN and order.hex_criteria:
                is_match = seymour_matches_hex_order(
                    seymour.hex_color,
                    seymour.item_name,
                    order.hex_criteria.match_type.value,
                    order.hex_criteria.hex_value,
                    order.hex_criteria.max_delta_e,
                    order.hex_criteria.piece_type,
                )
            if is_match:
                matching_seymours.append(seymour)

        if matching_seymours:
            fillable.append({"order": order, "matching_seymours": matching_seymours})

    await db.commit()

    reverse = sort_order == "desc"
    if sort_by == "price":
        fillable.sort(key=lambda x: x["order"].price, reverse=reverse)
    else:
        fillable.sort(key=lambda x: x["order"].expires_at, reverse=reverse)

    return fillable[skip:skip + limit]


def expire_sell_order_if_needed(order: SellOrder) -> SellOrder:
    if order.status == OrderStatus.ACTIVE and order.expires_at < datetime.utcnow():
        order.status = OrderStatus.EXPIRED
    return order


def build_sell_order_response(order: SellOrder) -> dict:
    seymour = order.seymour
    match = get_seymour_best_match(seymour.hex_color, seymour.item_name)
    
    return {
        "id": order.id,
        "price": order.price,
        "status": order.status,
        "created_at": order.created_at,
        "expires_at": order.expires_at,
        "seymour": {
            "id": seymour.id,
            "item_name": seymour.item_name,
            "hex_color": seymour.hex_color,
            "match": match,
        },
        "user": {
            "discord_id": order.user.discord_id,
            "username": order.user.username,
            "avatar_hash": order.user.avatar_hash,
        } if order.user else None,
    }


@router.post("/orders/sell", response_model=list[SellOrderResponse])
async def create_sell_orders(
    data: SellOrderCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    seymours_result = await db.execute(
        select(Seymour).where(Seymour.id.in_(data.seymour_ids), Seymour.user_id == user.id)
    )
    owned_seymours = {s.id: s for s in seymours_result.scalars().all()}

    missing = set(data.seymour_ids) - set(owned_seymours.keys())
    if missing:
        raise HTTPException(400, f"Seymours not found or not owned: {list(missing)}")

    active_listings_result = await db.execute(
        select(SellOrder.seymour_id)
        .where(SellOrder.seymour_id.in_(data.seymour_ids))
        .where(SellOrder.status == OrderStatus.ACTIVE)
        .where(SellOrder.expires_at > datetime.utcnow())
    )
    already_listed = set(active_listings_result.scalars().all())
    if already_listed:
        raise HTTPException(400, f"Seymours already listed: {list(already_listed)}")

    from datetime import timedelta
    expires_at = datetime.utcnow() + timedelta(hours=data.expires_hours)

    created_orders = []
    for seymour_id in data.seymour_ids:
        order = SellOrder(
            user_id=user.id,
            seymour_id=seymour_id,
            price=data.price,
            expires_at=expires_at,
        )
        db.add(order)
        created_orders.append(order)

    await db.commit()

    for order in created_orders:
        await db.refresh(order, ["seymour", "user"])

    return created_orders


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
    query = (
        select(SellOrder)
        .options(selectinload(SellOrder.seymour), selectinload(SellOrder.user))
        .where(SellOrder.status == OrderStatus.ACTIVE)
        .where(SellOrder.expires_at > datetime.utcnow())
    )

    if piece_type is not None:
        query = query.join(Seymour).where(Seymour.item_name == piece_type)

    if hex_color:
        if "Seymour" not in str(query):
            query = query.join(Seymour)
        query = query.where(Seymour.hex_color == hex_color.upper())

    if min_price is not None:
        query = query.where(SellOrder.price >= min_price)

    if max_price is not None:
        query = query.where(SellOrder.price <= max_price)

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    if sort_by != "delta_e":
        sort_col = SellOrder.price if sort_by == "price" else SellOrder.expires_at
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(sort_col))

    result = await db.execute(query)
    orders = list(result.scalars().all())

    for order in orders:
        expire_sell_order_if_needed(order)

    await db.commit()
    active_orders = [build_sell_order_response(o) for o in orders if o.status == OrderStatus.ACTIVE]

    if sort_by == "delta_e":
        reverse = sort_order == "desc"
        active_orders.sort(
            key=lambda x: x["seymour"]["match"]["delta_e"] if x["seymour"]["match"] else float("inf"),
            reverse=reverse
        )

    paginated = active_orders[skip:skip + limit]
    return SellOrderListResponse(orders=paginated, total=total or 0)


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
    query = (
        select(SellOrder)
        .options(selectinload(SellOrder.seymour), selectinload(SellOrder.user))
        .where(SellOrder.user_id == user.id)
    )

    if status:
        query = query.where(SellOrder.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    sort_col = SellOrder.price if sort_by == "price" else SellOrder.expires_at
    order_func = desc if sort_order == "desc" else asc
    query = query.order_by(order_func(sort_col)).offset(skip).limit(limit)

    result = await db.execute(query)
    orders = list(result.scalars().all())

    for order in orders:
        expire_sell_order_if_needed(order)

    await db.commit()

    return SellOrderListResponse(orders=[build_sell_order_response(o) for o in orders], total=total or 0)


@router.delete("/orders/sell/{order_id}")
async def cancel_sell_order(
    order_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(SellOrder).where(SellOrder.id == order_id, SellOrder.user_id == user.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Sell order not found")

    if order.status != OrderStatus.ACTIVE:
        raise HTTPException(400, "Sell order is not active")

    order.status = OrderStatus.CANCELLED
    await db.commit()

    return {"detail": "Sell order cancelled"}


@router.get("/orders/{order_id}", response_model=BuyOrderResponse)
async def get_order(
    order_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(BuyOrder)
        .options(selectinload(BuyOrder.set_criteria), selectinload(BuyOrder.hex_criteria))
        .where(BuyOrder.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")

    expire_if_needed(order)
    await db.commit()

    return order


@router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(BuyOrder).where(BuyOrder.id == order_id, BuyOrder.user_id == user.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")

    if order.status != OrderStatus.ACTIVE:
        raise HTTPException(400, "Order is not active")

    order.status = OrderStatus.CANCELLED
    await db.commit()

    return {"detail": "Order cancelled"}
