from enum import Enum as PyEnum
from sqlalchemy import Integer, String, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ItemName(PyEnum):
    CASHMERE_JACKET = "Cashmere Jacket"
    OXFORD_SHOES = "Oxford Shoes"
    SATIN_TROUSERS = "Satin Trousers"
    VELVET_TOP_HAT = "Velvet Top Hat"


class Seymour(Base):
    __tablename__ = "seymours"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    item_name: Mapped[ItemName] = mapped_column(Enum(ItemName, values_callable=lambda x: [e.value for e in x]))
    hex_color: Mapped[str] = mapped_column(String(6))

    user: Mapped["User"] = relationship(back_populates="seymours")

