from sqlalchemy import String, BigInteger, Boolean, SmallInteger, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from app.database import Base


class ActiveAuction(Base):
    __tablename__ = "active_auctions"
    __table_args__ = (
        Index("ix_active_auctions_piece", "piece"),
        Index("ix_active_auctions_hex_color", "hex_color"),
        Index("ix_active_auctions_price", "price"),
        Index("ix_active_auctions_end_time", "end_time"),
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
    last_updated: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    bids: Mapped[list["Bid"]] = relationship(back_populates="auction", cascade="all, delete-orphan")


class EndedAuction(Base):
    __tablename__ = "ended_auctions"
    __table_args__ = (
        Index("ix_ended_auctions_piece", "piece"),
        Index("ix_ended_auctions_hex_color", "hex_color"),
        Index("ix_ended_auctions_price", "price"),
        Index("ix_ended_auctions_timestamp", "timestamp"),
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
