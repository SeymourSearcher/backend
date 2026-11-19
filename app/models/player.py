from datetime import datetime
from sqlalchemy import String, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.database import Base


class Player(Base):
    __tablename__ = "players"
    # The refresh worker's "oldest N" query orders by this; without the index
    # it filesorts the whole table every cycle.
    __table_args__ = (
        Index("ix_players_last_updated", "last_updated"),
    )

    uuid: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(16))
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlayerLookupFailure(Base):
    """A uuid whose latest upstream lookup failed, held back from the refresh
    queues for a while. See app/services/player_sync.py."""
    __tablename__ = "player_lookup_failures"

    uuid: Mapped[str] = mapped_column(String(32), primary_key=True)
    failed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
