from sqlalchemy import String, BigInteger, Boolean, Float, SmallInteger, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from app.clock import utcnow
from app.database import Base


class ActiveAuction(Base):
    __tablename__ = "active_auctions"
    __table_args__ = (
        Index("ix_active_auctions_piece", "piece"),
        Index("ix_active_auctions_hex_color", "hex_color"),
        Index("ix_active_auctions_price", "price"),
        Index("ix_active_auctions_end_time", "end_time"),
        Index("ix_active_tier_keys_price", "tier_keys", "price"),
        Index("ix_active_tier_keys_end_time", "tier_keys", "end_time"),
        Index("ix_active_tier_all_price", "tier_all", "price"),
        Index("ix_active_tier_all_end_time", "tier_all", "end_time"),
        Index("ix_active_target_keys_price", "target_hex_keys", "price"),
        Index("ix_active_target_all_price", "target_hex_all", "price"),
    )

    auction_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seller_uuid: Mapped[str] = mapped_column(String(32))
    price: Mapped[int] = mapped_column(BigInteger)
    is_bin: Mapped[bool] = mapped_column(Boolean)
    start_time: Mapped[int] = mapped_column(BigInteger)
    end_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    item_uuid: Mapped[str] = mapped_column(String(36))
    hex_color: Mapped[str] = mapped_column(String(6))
    piece: Mapped[int] = mapped_column(SmallInteger)
    last_updated: Mapped[datetime] = mapped_column(default=utcnow)

    # Nullable so the migration can add them without a blocking backfill; see
    # scripts/backfill_auction_matches.py.
    target_hex_keys: Mapped[str | None] = mapped_column(String(6), nullable=True)
    delta_e_keys: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier_keys: Mapped[str | None] = mapped_column(String(2), nullable=True)
    target_hex_all: Mapped[str | None] = mapped_column(String(6), nullable=True)
    delta_e_all: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier_all: Mapped[str | None] = mapped_column(String(2), nullable=True)

    bids: Mapped[list["Bid"]] = relationship(back_populates="auction", cascade="all, delete-orphan")


class EndedAuction(Base):
    __tablename__ = "ended_auctions"
    __table_args__ = (
        Index("ix_ended_auctions_piece", "piece"),
        Index("ix_ended_auctions_hex_color", "hex_color"),
        Index("ix_ended_auctions_price", "price"),
        Index("ix_ended_auctions_timestamp", "timestamp"),
        Index("ix_ended_tier_keys_price", "tier_keys", "price"),
        Index("ix_ended_tier_keys_timestamp", "tier_keys", "timestamp"),
        Index("ix_ended_tier_all_price", "tier_all", "price"),
        Index("ix_ended_tier_all_timestamp", "tier_all", "timestamp"),
        Index("ix_ended_target_keys_price", "target_hex_keys", "price"),
        Index("ix_ended_target_all_price", "target_hex_all", "price"),
    )

    auction_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seller_uuid: Mapped[str] = mapped_column(String(32))
    buyer_uuid: Mapped[str] = mapped_column(String(32))
    price: Mapped[int] = mapped_column(BigInteger)
    is_bin: Mapped[bool] = mapped_column(Boolean)
    item_uuid: Mapped[str] = mapped_column(String(36))
    hex_color: Mapped[str] = mapped_column(String(6))
    piece: Mapped[int] = mapped_column(SmallInteger)
    timestamp: Mapped[int] = mapped_column(BigInteger)

    target_hex_keys: Mapped[str | None] = mapped_column(String(6), nullable=True)
    delta_e_keys: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier_keys: Mapped[str | None] = mapped_column(String(2), nullable=True)
    target_hex_all: Mapped[str | None] = mapped_column(String(6), nullable=True)
    delta_e_all: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier_all: Mapped[str | None] = mapped_column(String(2), nullable=True)


class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (
        Index("ix_bids_auction_id", "auction_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    auction_id: Mapped[str] = mapped_column(String(32), ForeignKey("active_auctions.auction_id", ondelete="CASCADE"))
    bidder_uuid: Mapped[str] = mapped_column(String(32))
    amount: Mapped[int] = mapped_column(BigInteger)
    timestamp: Mapped[int] = mapped_column(BigInteger)

    auction: Mapped["ActiveAuction"] = relationship(back_populates="bids")
