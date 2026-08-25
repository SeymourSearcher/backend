from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.models import OrderStatus, SellOrder, User
# Re-exported so routers reach the filter type through this service rather than
# the repository layer.
from app.repositories.marketplace_repo import SellOrderFilters, SellOrderRepository
from app.repositories.seymour_repo import SeymourRepository
from app.schemas.marketplace import SellOrderCreate
from app.services.marketplace import build_sell_order_response, is_expired


async def create(db: AsyncSession, user: User, data: SellOrderCreate) -> list[SellOrder]:
    owned = await SeymourRepository(db).lock_owned(data.seymour_ids, user.id)

    missing = set(data.seymour_ids) - set(owned)
    if missing:
        raise InvalidInputError(f"Seymours not found or not owned: {list(missing)}")

    orders = SellOrderRepository(db)
    already_listed = await orders.listed_seymour_ids(data.seymour_ids)
    if already_listed:
        raise ConflictError(f"Seymours already listed: {list(already_listed)}")

    expires_at = utcnow() + timedelta(hours=data.expires_hours)
    created = [
        await orders.add(user.id, seymour_id, data.price, expires_at)
        for seymour_id in data.seymour_ids
    ]

    await db.commit()
    for order in created:
        await db.refresh(order, ["seymour", "user"])

    return created


async def list_active(
    db: AsyncSession,
    filters: SellOrderFilters,
    sort_by: str,
    sort_order: str,
    skip: int,
    limit: int,
) -> tuple[list[dict], int]:
    orders = SellOrderRepository(db)

    if sort_by == "delta_e":
        # delta_e is derived per Seymour rather than stored, so this sort cannot
        # be pushed into SQL and has to page in Python over the full result set.
        rows, total = await orders.list_active_unpaginated(filters)
        responses = [build_sell_order_response(o) for o in rows]
        responses.sort(
            key=lambda x: x["seymour"]["match"]["delta_e"] if x["seymour"]["match"] else float("inf"),
            reverse=sort_order == "desc",
        )
        return responses[skip:skip + limit], total

    rows, total = await orders.list_active(filters, sort_by, sort_order, skip, limit)
    return [build_sell_order_response(o) for o in rows], total


async def list_for_user(
    db: AsyncSession,
    user: User,
    status: OrderStatus | None,
    sort_by: str,
    sort_order: str,
    skip: int,
    limit: int,
) -> tuple[list[dict], int]:
    rows, total = await SellOrderRepository(db).list_for_user(
        user.id, status, sort_by, sort_order, skip, limit
    )

    responses = []
    for order in rows:
        response = build_sell_order_response(order)
        if is_expired(order):
            response["status"] = OrderStatus.EXPIRED
        responses.append(response)

    return responses, total


async def cancel(db: AsyncSession, user: User, order_id: int) -> None:
    order = await SellOrderRepository(db).get_owned(order_id, user.id)
    if not order:
        raise NotFoundError("Sell order not found")
    if order.status != OrderStatus.ACTIVE:
        raise InvalidInputError("Sell order is not active")

    order.status = OrderStatus.CANCELLED
    await db.commit()
