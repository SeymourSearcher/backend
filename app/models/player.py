from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.database import Base


class Player(Base):
    __tablename__ = "players"

    uuid: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(16))
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
