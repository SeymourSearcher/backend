from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    discord_id: Mapped[str] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255))
    avatar_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_uploaded: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    seymours: Mapped[list["Seymour"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    buy_orders: Mapped[list["BuyOrder"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sell_orders: Mapped[list["SellOrder"]] = relationship(back_populates="user", cascade="all, delete-orphan")

