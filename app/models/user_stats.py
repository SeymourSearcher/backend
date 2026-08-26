from datetime import datetime
from sqlalchemy import Integer, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.database import Base

# The tallied columns, in response order. Canonical here because these are the
# columns; StatsResponse and the compute path both follow it, and test_stats.py
# pins them together.
STAT_KEYS = (
    "stars", "t1", "t2", "t3", "words", "dupes",
    "total_sets", "dyed_sets", "total_seymours",
)


class UserStats(Base):
    """One row per user whose Seymours have been tallied.

    Derived entirely from that user's seymours, and only ever recomputed when
    they change -- see app/services/rankings.py. A missing row means "not
    computed yet", which is why the columns are not nullable.
    """
    __tablename__ = "user_stats"
    # The leaderboard's only ordering.
    __table_args__ = (
        Index("ix_user_stats_total_seymours", "total_seymours"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    stars: Mapped[int] = mapped_column(Integer, default=0)
    t1: Mapped[int] = mapped_column(Integer, default=0)
    t2: Mapped[int] = mapped_column(Integer, default=0)
    t3: Mapped[int] = mapped_column(Integer, default=0)
    words: Mapped[int] = mapped_column(Integer, default=0)
    dupes: Mapped[int] = mapped_column(Integer, default=0)
    total_sets: Mapped[int] = mapped_column(Integer, default=0)
    dyed_sets: Mapped[int] = mapped_column(Integer, default=0)
    total_seymours: Mapped[int] = mapped_column(Integer, default=0)

    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="stats")
