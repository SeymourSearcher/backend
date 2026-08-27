from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError, NotFoundError
from app.models import BuyOrder, OrderStatus, OrderType, Seymour, User
# Re-exported so routers reach the filter type through this service rather than
# the repository layer.
from app.repositories.marketplace_repo import BuyOrderFilters, BuyOrderRepository
from app.repositories.seymour_repo import SeymourRepository
from app.schemas.marketplace import BuyOrderCreate, BuyOrderResponse
from app.services.marketplace import (
    is_valid_piece_for_set,
    is_valid_set,
    seymour_matches_hex_order,
    seymour_matches_set_order,
    with_derived_status,
)


def _validate(data: BuyOrderCreate) -> None:
    if data.order_type == OrderType.SET_PIECE:
        if not data.set_criteria:
            raise InvalidInputError("set_criteria required for SET_PIECE orders")
        if not is_valid_set(data.set_criteria.set_name):
            raise InvalidInputError(f"Invalid set name: {data.set_criteria.set_name}")
        for piece_type in data.set_criteria.piece_types:
            if not is_valid_piece_for_set(data.set_criteria.set_name, piece_type):
                raise InvalidInputError(
                    f"Piece type {piece_type} is not available for this set"
                )
    elif data.order_type == OrderType.HEX_PATTERN and not data.hex_criteria:
        raise InvalidInputError("hex_criteria required for HEX_PATTERN orders")


def _matches(seymour: Seymour, order: BuyOrder) -> bool:
    if order.order_type == OrderType.SET_PIECE and order.set_criteria:
        return seymour_matches_set_order(
            seymour.hex_color,
            seymour.item_name,
            order.set_criteria.set_name,
            order.set_criteria.piece_types,
            order.set_criteria.tier.value,
        )
    if order.order_type == OrderType.HEX_PATTERN and order.hex_criteria:
        return seymour_matches_hex_order(
            seymour.hex_color,
            seymour.item_name,
            order.hex_criteria.match_type.value,
            order.hex_criteria.hex_value,
            order.hex_criteria.max_delta_e,
            order.hex_criteria.piece_type,
        )
    return False


async def create(db: AsyncSession, user: User, data: BuyOrderCreate) -> BuyOrder:
    _validate(data)

    orders = BuyOrderRepository(db)
    order = await orders.add(user.id, data.price, data.order_type)

    if data.set_criteria:
        await orders.add_set_criteria(
            order.id,
            data.set_criteria.set_name,
            data.set_criteria.piece_types,
            data.set_criteria.tier,
        )
    if data.hex_criteria:
        await orders.add_hex_criteria(
            order.id,
            data.hex_criteria.match_type,
            data.hex_criteria.hex_value,
            data.hex_criteria.max_delta_e,
            data.hex_criteria.piece_type,
        )

    await db.commit()
    await db.refresh(order, ["set_criteria", "hex_criteria"])
    return order


async def list_active(
    db: AsyncSession,
    filters: BuyOrderFilters,
    sort_by: str,
    sort_order: str,
    skip: int,
    limit: int,
) -> tuple[list[BuyOrder], int]:
    return await BuyOrderRepository(db).list_active(filters, sort_by, sort_order, skip, limit)


async def list_for_user(
    db: AsyncSession,
    user: User,
    status: OrderStatus | None,
    sort_by: str,
    sort_order: str,
    skip: int,
    limit: int,
) -> tuple[list[BuyOrderResponse], int]:
    orders, total = await BuyOrderRepository(db).list_for_user(
        user.id, status, sort_by, sort_order, skip, limit
    )
    return [
        with_derived_status(BuyOrderResponse.model_validate(o), o) for o in orders
    ], total


async def list_matching(
    db: AsyncSession, user: User, seymour_id: int, skip: int, limit: int
) -> tuple[list[BuyOrder], int]:
    seymour = await SeymourRepository(db).get_owned(seymour_id, user.id)
    if not seymour:
        raise NotFoundError("Seymour not found")

    orders = await BuyOrderRepository(db).list_active_excluding(user.id)
    matching = [order for order in orders if _matches(seymour, order)]
    matching.sort(key=lambda o: o.price, reverse=True)

    return matching[skip:skip + limit], len(matching)


async def list_fillable(
    db: AsyncSession, user: User, sort_by: str, sort_order: str, skip: int, limit: int
) -> list[dict]:
    # Orders first: there are usually none, and loading the user's seymours to
    # match against nothing is the whole cost of the request (180ms at 17.5k rows).
    orders = await BuyOrderRepository(db).list_active_excluding(user.id)
    if not orders:
        return []

    seymours = await SeymourRepository(db).list_for_user(user.id)

    fillable = []
    for order in orders:
        matching = [s for s in seymours if _matches(s, order)]
        if matching:
            fillable.append({"order": order, "matching_seymours": matching})

    key = (lambda x: x["order"].price) if sort_by == "price" else (lambda x: x["order"].expires_at)
    fillable.sort(key=key, reverse=sort_order == "desc")

    return fillable[skip:skip + limit]


async def get(db: AsyncSession, order_id: int) -> BuyOrderResponse:
    order = await BuyOrderRepository(db).get(order_id)
    if not order:
        raise NotFoundError("Order not found")
    return with_derived_status(BuyOrderResponse.model_validate(order), order)


async def cancel(db: AsyncSession, user: User, order_id: int) -> None:
    order = await BuyOrderRepository(db).get_owned(order_id, user.id)
    if not order:
        raise NotFoundError("Order not found")
    if order.status != OrderStatus.ACTIVE:
        raise InvalidInputError("Order is not active")

    order.status = OrderStatus.CANCELLED
    await db.commit()
