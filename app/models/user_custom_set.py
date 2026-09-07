from sqlalchemy import Float, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Stored per user, best first. One more than the homepage's per-user cap, so a
# user whose best set is already shown still has a spare to fall back on.
SETS_PER_USER = 3

# Indexed by app.pieces.Piece, which is what find_custom_sets reports against.
PIECE_HEX_COLUMNS = ("helmet_hex", "chestplate_hex", "leggings_hex", "boots_hex")


class UserCustomSet(Base):
    """One of a user's closest self-matched four-piece sets.

    Derived entirely from that user's Seymours and recomputed only when they
    change -- see app/services/custom_sets.py. Persisted for the same reason
    user_stats is: the gallery reads across every user, and computing it on
    request makes one visitor pay for everyone's KD-tree walk.
    """
    __tablename__ = "user_custom_sets"
    __table_args__ = (
        # The gallery's only query: the per-user cap is a range on the leading
        # column, then the survivors order globally.
        Index("ix_user_custom_sets_rank_delta", "set_rank", "avg_delta_e"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # 0 is the user's own best. Not `rank`, which MySQL 8 reserves.
    set_rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True)

    avg_delta_e: Mapped[float] = mapped_column(Float)

    helmet_hex: Mapped[str] = mapped_column(String(6))
    chestplate_hex: Mapped[str] = mapped_column(String(6))
    leggings_hex: Mapped[str] = mapped_column(String(6))
    boots_hex: Mapped[str] = mapped_column(String(6))

    user: Mapped["User"] = relationship(back_populates="custom_sets")

    @property
    def hexes(self) -> list[str]:
        return [getattr(self, column) for column in PIECE_HEX_COLUMNS]
