from enum import Enum as PyEnum
from sqlalchemy import Integer, String, ForeignKey, SmallInteger, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ItemName(PyEnum):
    VELVET_TOP_HAT = 0
    CASHMERE_JACKET = 1
    SATIN_TROUSERS = 2
    OXFORD_SHOES = 3


class Seymour(Base):
    __tablename__ = "seymours"
    __table_args__ = (
        Index("ix_seymours_user_item", "user_id", "item_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_name: Mapped[int] = mapped_column(SmallInteger)
    hex_color: Mapped[str] = mapped_column(String(6))

    user: Mapped["User"] = relationship(back_populates="seymours")
    sell_order: Mapped["SellOrder"] = relationship(back_populates="seymour", uselist=False)

