from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.models import Player
from app.repositories.base import BaseRepository


class PlayerRepository(BaseRepository):
    async def by_uuid(self, uuids: list[str]) -> dict[str, Player]:
        rows = await self.session.execute(select(Player).where(Player.uuid.in_(uuids)))
        return {p.uuid: p for p in rows.scalars().all()}

    async def upsert(self, rows: list[dict]) -> None:
        """MySQL ON DUPLICATE KEY UPDATE: concurrent readers race on the same uuids."""
        stmt = insert(Player).values(rows)
        await self.session.execute(
            stmt.on_duplicate_key_update(
                username=stmt.inserted.username,
                last_updated=stmt.inserted.last_updated,
            )
        )
