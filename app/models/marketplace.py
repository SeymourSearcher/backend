from enum import Enum as PyEnum
from datetime import datetime, timedelta
from sqlalchemy import Integer, String, ForeignKey, SmallInteger, Float, DateTime, Index, Enum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.database import Base


class OrderType(PyEnum):
    SET_PIECE = "set_piece"
    HEX_PATTERN = "hex_pattern"


class OrderStatus(PyEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class HexMatchType(PyEnum):
    EXACT = "exact"
    PATTERN = "pattern"
    DELTA_E = "delta_e"


class Tier(PyEnum):
    T1 = "t1"
    T2 = "t2"
    T3 = "t3"


def default_expiry():
    return utcnow() + timedelta(hours=48)


class BuyOrder(Base):
    __tablename__ = "buy_orders"
    __table_args__ = (
        Index("ix_buy_orders_user_status", "user_id", "status"),
        Index("ix_buy_orders_status_type", "status", "order_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    price: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType))
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=default_expiry)

    user: Mapped["User"] = relationship(back_populates="buy_orders")
    set_criteria: Mapped["BuyOrderSetCriteria"] = relationship(back_populates="order", uselist=False, cascade="all, delete-orphan")
    hex_criteria: Mapped["BuyOrderHexCriteria"] = relationship(back_populates="order", uselist=False, cascade="all, delete-orphan")


class BuyOrderSetCriteria(Base):
    __tablename__ = "buy_order_set_criteria"
    __table_args__ = (
        Index("ix_set_criteria_lookup", "set_name", "tier"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("buy_orders.id", ondelete="CASCADE"), unique=True)
    set_name: Mapped[str] = mapped_column(String(100))
    piece_types: Mapped[list[int]] = mapped_column(JSON)
    tier: Mapped[Tier] = mapped_column(Enum(Tier))

    order: Mapped["BuyOrder"] = relationship(back_populates="set_criteria")


class BuyOrderHexCriteria(Base):
    __tablename__ = "buy_order_hex_criteria"
    __table_args__ = (
        Index("ix_hex_criteria_lookup", "match_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("buy_orders.id", ondelete="CASCADE"), unique=True)
    match_type: Mapped[HexMatchType] = mapped_column(Enum(HexMatchType))
    hex_value: Mapped[str] = mapped_column(String(6))
    max_delta_e: Mapped[float | None] = mapped_column(Float, nullable=True)
    piece_type: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    order: Mapped["BuyOrder"] = relationship(back_populates="hex_criteria")


class SellOrder(Base):
    __tablename__ = "sell_orders"
    __table_args__ = (
        Index("ix_sell_orders_user_status", "user_id", "status"),
        Index("ix_sell_orders_status", "status"),
        Index("ix_sell_orders_seymour_status", "seymour_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seymour_id: Mapped[int] = mapped_column(Integer, ForeignKey("seymours.id", ondelete="CASCADE"), index=True)
    price: Mapped[int] = mapped_column(Integer)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=default_expiry)

    user: Mapped["User"] = relationship(back_populates="sell_orders")
    seymour: Mapped["Seymour"] = relationship(back_populates="sell_order")
