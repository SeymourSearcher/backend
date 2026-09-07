"""Buy/sell order queries.

status_filter lives here rather than in a service because expiry is derived
rather than persisted, so an ACTIVE/EXPIRED filter has to become a SQL
predicate — a Python check would only see the rows the database already sent.
"""
from dataclasses import dataclass

from sqlalchemy import String, and_, asc, cast, desc, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.clock import utcnow
from app.models import (
    BuyOrder,
    BuyOrderHexCriteria,
    BuyOrderSetCriteria,
    OrderStatus,
    OrderType,
    SellOrder,
    Seymour,
    Tier,
)
from app.repositories.base import BaseRepository


def status_filter(model, status: OrderStatus | None):
    """Expiry is derived, so an ACTIVE/EXPIRED filter must account for it in SQL."""
    now = utcnow()
    if status is None:
        return None
    if status == OrderStatus.ACTIVE:
        return and_(model.status == OrderStatus.ACTIVE, model.expires_at > now)
    if status == OrderStatus.EXPIRED:
        return or_(
            model.status == OrderStatus.EXPIRED,
            and_(model.status == OrderStatus.ACTIVE, model.expires_at <= now),
        )
    return model.status == status


def _ordered(query: Select, model, sort_by: str, sort_order: str) -> Select:
    column = model.price if sort_by == "price" else model.expires_at
    return query.order_by((desc if sort_order == "desc" else asc)(column))


@dataclass(frozen=True)
class BuyOrderFilters:
    order_type: OrderType | None = None
    set_name: str | None = None
    piece_types: list[int] | None = None
    tier: Tier | None = None


@dataclass(frozen=True)
class SellOrderFilters:
    piece_type: int | None = None
    hex_color: str | None = None
    min_price: int | None = None
    max_price: int | None = None


# Serialising BuyOrderResponse touches .user; without this the lazy load raises
# MissingGreenlet on the async session.
_BUY_LOADS = (
    selectinload(BuyOrder.set_criteria),
    selectinload(BuyOrder.hex_criteria),
    selectinload(BuyOrder.user),
)

_SELL_LOADS = (selectinload(SellOrder.seymour), selectinload(SellOrder.user))


class BuyOrderRepository(BaseRepository):
    @staticmethod
    def _select() -> Select:
        return select(BuyOrder).options(*_BUY_LOADS)

    @classmethod
    def _active(cls) -> Select:
        return (
            cls._select()
            .where(BuyOrder.status == OrderStatus.ACTIVE)
            .where(BuyOrder.expires_at > utcnow())
        )

    async def list_active(
        self, filters: BuyOrderFilters, sort_by: str, sort_order: str, skip: int, limit: int
    ) -> tuple[list[BuyOrder], int]:
        query = self._active()
        if filters.order_type:
            query = query.where(BuyOrder.order_type == filters.order_type)

        joined_set_criteria = False
        if filters.set_name:
            query = query.join(BuyOrderSetCriteria).where(
                BuyOrderSetCriteria.set_name == filters.set_name
            )
            joined_set_criteria = True

        if filters.tier:
            if not joined_set_criteria:
                query = query.join(BuyOrderSetCriteria)
                joined_set_criteria = True
            query = query.where(BuyOrderSetCriteria.tier == filters.tier)

        if filters.piece_types:
            if not joined_set_criteria:
                query = query.outerjoin(
                    BuyOrderSetCriteria, BuyOrder.id == BuyOrderSetCriteria.order_id, full=False
                )
            query = query.outerjoin(
                BuyOrderHexCriteria, BuyOrder.id == BuyOrderHexCriteria.order_id, full=False
            )
            query = query.where(
                or_(
                    *[
                        func.instr(cast(BuyOrderSetCriteria.piece_types, String), str(pt)) > 0
                        for pt in filters.piece_types
                    ],
                    BuyOrderHexCriteria.piece_type.in_(filters.piece_types),
                )
            )

        total = await self._total(query)
        rows = await self.session.execute(
            self._paginate(_ordered(query, BuyOrder, sort_by, sort_order), skip, limit)
        )
        return list(rows.scalars().all()), total

    async def list_for_user(
        self,
        user_id: int,
        status: OrderStatus | None,
        sort_by: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[list[BuyOrder], int]:
        query = self._select().where(BuyOrder.user_id == user_id)
        if status:
            query = query.where(status_filter(BuyOrder, status))

        total = await self._total(query)
        rows = await self.session.execute(
            self._paginate(_ordered(query, BuyOrder, sort_by, sort_order), skip, limit)
        )
        return list(rows.scalars().all()), total

    async def list_active_excluding(self, user_id: int) -> list[BuyOrder]:
        """Every open order from someone else; matching happens in Python."""
        rows = await self.session.execute(self._active().where(BuyOrder.user_id != user_id))
        return list(rows.scalars().all())

    async def get(self, order_id: int) -> BuyOrder | None:
        return (
            await self.session.execute(self._select().where(BuyOrder.id == order_id))
        ).scalar_one_or_none()

    async def get_owned(self, order_id: int, user_id: int) -> BuyOrder | None:
        return (
            await self.session.execute(
                select(BuyOrder).where(BuyOrder.id == order_id, BuyOrder.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def add(self, user_id: int, price: int, order_type: OrderType) -> BuyOrder:
        order = BuyOrder(user_id=user_id, price=price, order_type=order_type)
        self.session.add(order)
        await self.session.flush()
        return order

    async def add_set_criteria(
        self, order_id: int, set_name: str, piece_types: list[int], tier: Tier
    ) -> None:
        self.session.add(
            BuyOrderSetCriteria(
                order_id=order_id, set_name=set_name, piece_types=piece_types, tier=tier
            )
        )
        await self.session.flush()

    async def add_hex_criteria(
        self,
        order_id: int,
        match_type,
        hex_value: str,
        max_delta_e: float | None,
        piece_type: int | None,
    ) -> None:
        self.session.add(
            BuyOrderHexCriteria(
                order_id=order_id,
                match_type=match_type,
                hex_value=hex_value,
                max_delta_e=max_delta_e,
                piece_type=piece_type,
            )
        )
        await self.session.flush()


class SellOrderRepository(BaseRepository):
    @staticmethod
    def _select() -> Select:
        return select(SellOrder).options(*_SELL_LOADS)

    @classmethod
    def _active(cls, filters: SellOrderFilters) -> Select:
        query = (
            cls._select()
            .where(SellOrder.status == OrderStatus.ACTIVE)
            .where(SellOrder.expires_at > utcnow())
        )
        if filters.piece_type is not None or filters.hex_color:
            query = query.join(Seymour)
        if filters.piece_type is not None:
            query = query.where(Seymour.item_name == filters.piece_type)
        if filters.hex_color:
            query = query.where(Seymour.hex_color == filters.hex_color.upper())
        if filters.min_price is not None:
            query = query.where(SellOrder.price >= filters.min_price)
        if filters.max_price is not None:
            query = query.where(SellOrder.price <= filters.max_price)
        return query

    async def list_active(
        self, filters: SellOrderFilters, sort_by: str, sort_order: str, skip: int, limit: int
    ) -> tuple[list[SellOrder], int]:
        query = self._active(filters)
        total = await self._total(query)
        rows = await self.session.execute(
            self._paginate(_ordered(query, SellOrder, sort_by, sort_order), skip, limit)
        )
        return list(rows.scalars().all()), total

    async def list_active_unpaginated(
        self, filters: SellOrderFilters
    ) -> tuple[list[SellOrder], int]:
        """Sorting by delta_e can't be pushed into SQL; see services/sell_orders.py."""
        query = self._active(filters)
        total = await self._total(query)
        rows = await self.session.execute(query)
        return list(rows.scalars().all()), total

    async def list_for_user(
        self,
        user_id: int,
        status: OrderStatus | None,
        sort_by: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[list[SellOrder], int]:
        query = self._select().where(SellOrder.user_id == user_id)
        if status:
            query = query.where(status_filter(SellOrder, status))

        total = await self._total(query)
        rows = await self.session.execute(
            self._paginate(_ordered(query, SellOrder, sort_by, sort_order), skip, limit)
        )
        return list(rows.scalars().all()), total

    async def get_owned(self, order_id: int, user_id: int) -> SellOrder | None:
        return (
            await self.session.execute(
                select(SellOrder).where(SellOrder.id == order_id, SellOrder.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def count_active_for_user(self, user_id: int) -> int:
        """Listings that would be lost if this user's Seymours went.

        sell_orders.seymour_id cascades on delete, so wiping a collection takes
        its listings with it -- silently, in MySQL, where nothing here can count
        them afterwards.
        """
        return await self.session.scalar(
            select(func.count())
            .select_from(SellOrder)
            .where(SellOrder.user_id == user_id)
            .where(status_filter(SellOrder, OrderStatus.ACTIVE))
        ) or 0

    async def listed_seymour_ids(self, seymour_ids: list[int]) -> set[int]:
        """Which of these are already up for sale. Must be a locking read.

        Under REPEATABLE READ the consistent snapshot is taken at the
        transaction's first plain SELECT -- which, since get_current_user loads
        the user on the request's own session, has already happened by the time
        any handler runs. A plain SELECT here would therefore read state from
        before the caller blocked on lock_owned, miss the listing the winner
        just committed, and list the same Seymour twice. A locking read always
        sees the latest committed row.

        Safe against deadlock because callers hold FOR UPDATE on the Seymour
        rows first, so two transactions can never both reach this line for the
        same id.
        """
        rows = await self.session.execute(
            select(SellOrder.seymour_id)
            .where(SellOrder.seymour_id.in_(seymour_ids))
            .where(SellOrder.status == OrderStatus.ACTIVE)
            .where(SellOrder.expires_at > utcnow())
            .with_for_update()
        )
        return set(rows.scalars().all())

    async def add(self, user_id: int, seymour_id: int, price: int, expires_at) -> SellOrder:
        order = SellOrder(
            user_id=user_id, seymour_id=seymour_id, price=price, expires_at=expires_at
        )
        self.session.add(order)
        await self.session.flush()
        return order
