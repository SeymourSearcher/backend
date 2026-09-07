from sqlalchemy import delete, insert, select

from app.models import Seymour, User
from app.repositories.base import BaseRepository


class SeymourRepository(BaseRepository):
    async def list_for_user(self, user_id: int) -> list[Seymour]:
        rows = await self.session.execute(select(Seymour).where(Seymour.user_id == user_id))
        return list(rows.scalars().all())

    async def list_owner_hexes(self) -> list[tuple[int, str, int, str | None, str, int]]:
        """(user_id, username, discord_id, avatar_hash, hex_color, item_name) for every Seymour.

        Columns rather than entities: the hex page walks the whole table, and
        building ORM instances for rows it mostly discards dominated the request.
        """
        rows = await self.session.execute(
            select(
                User.id,
                User.username,
                User.discord_id,
                User.avatar_hash,
                Seymour.hex_color,
                Seymour.item_name,
            ).join(Seymour)
        )
        return list(rows.all())

    async def delete_for_user(self, user_id: int) -> int:
        """Returns how many rows went. A bulk DELETE reports its own rowcount,
        so counting first would be a second query for the same number."""
        result = await self.session.execute(delete(Seymour).where(Seymour.user_id == user_id))
        return result.rowcount

    async def remove(self, seymour: Seymour) -> None:
        await self.session.delete(seymour)

    async def add_many(self, values: list[dict]) -> None:
        await self.session.execute(insert(Seymour), values)

    async def get_owned(self, seymour_id: int, user_id: int) -> Seymour | None:
        return (
            await self.session.execute(
                select(Seymour).where(Seymour.id == seymour_id, Seymour.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def lock_owned(self, seymour_ids: list[int], user_id: int) -> dict[int, Seymour]:
        """Locks the rows for the transaction.

        The caller's "already listed" check is otherwise a check-then-insert that
        two concurrent requests both pass, double-listing the same Seymour.
        """
        rows = await self.session.execute(
            select(Seymour)
            .where(Seymour.id.in_(seymour_ids), Seymour.user_id == user_id)
            .with_for_update()
        )
        return {s.id: s for s in rows.scalars().all()}
