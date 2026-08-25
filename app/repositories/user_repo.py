from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def list_with_seymours(self) -> list[User]:
        """Every user who owns at least one Seymour, with them eagerly loaded."""
        rows = await self.session.execute(
            select(User).options(selectinload(User.seymours)).where(User.seymours.any())
        )
        return list(rows.scalars().all())

    async def get_by_discord_id(self, discord_id: int) -> User | None:
        return (
            await self.session.execute(select(User).where(User.discord_id == discord_id))
        ).scalar_one_or_none()

    async def add(self, discord_id: int, username: str, avatar_hash: str | None) -> User:
        user = User(discord_id=discord_id, username=username, avatar_hash=avatar_hash)
        self.session.add(user)
        await self.session.flush()
        return user
