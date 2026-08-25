from sqlalchemy import delete, insert, select

from app.models import Seymour, User
from app.repositories.base import BaseRepository


class SeymourRepository(BaseRepository):
    async def list_for_user(self, user_id: int) -> list[Seymour]:
        rows = await self.session.execute(select(Seymour).where(Seymour.user_id == user_id))
        return list(rows.scalars().all())

    async def list_with_users(self) -> list[tuple[User, Seymour]]:
        return list((await self.session.execute(select(User, Seymour).join(Seymour))).all())

    async def delete_for_user(self, user_id: int) -> None:
        await self.session.execute(delete(Seymour).where(Seymour.user_id == user_id))

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
