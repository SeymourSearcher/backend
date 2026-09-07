from sqlalchemy import delete, insert, select

from app.models import User, UserCustomSet
from app.repositories.base import BaseRepository


class UserCustomSetRepository(BaseRepository):
    async def replace_for_user(self, user_id: int, rows: list[dict]) -> None:
        """Delete-then-insert rather than upsert: the new set count can be lower
        than the old one, so surviving rows would otherwise be stale."""
        await self.session.execute(
            delete(UserCustomSet).where(UserCustomSet.user_id == user_id)
        )
        if rows:
            await self.session.execute(insert(UserCustomSet), rows)

    async def list_best(self, per_user: int, limit: int) -> list[tuple[User, UserCustomSet]]:
        """The closest sets sitewide, at most `per_user` from any one collector.

        The cap is a predicate on set_rank rather than a window function because
        the rank is assigned at write time.
        """
        rows = await self.session.execute(
            select(User, UserCustomSet)
            .join(UserCustomSet, User.id == UserCustomSet.user_id)
            .where(UserCustomSet.set_rank < per_user)
            .order_by(UserCustomSet.avg_delta_e, UserCustomSet.user_id)
            .limit(limit)
        )
        return [(user, custom_set) for user, custom_set in rows.all()]
