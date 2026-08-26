from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert

from app.models import User, UserStats
from app.models.user_stats import STAT_KEYS
from app.repositories.base import BaseRepository


class UserStatsRepository(BaseRepository):
    async def upsert(self, user_id: int, stats: dict) -> None:
        values = {"user_id": user_id, **{key: stats[key] for key in STAT_KEYS}}
        statement = insert(UserStats).values(**values)
        await self.session.execute(
            statement.on_duplicate_key_update(
                **{key: statement.inserted[key] for key in STAT_KEYS},
                computed_at=func.now(),
            )
        )

    async def delete_for_user(self, user_id: int) -> None:
        stats = await self.session.get(UserStats, user_id)
        if stats:
            await self.session.delete(stats)

    async def get(self, user_id: int) -> UserStats | None:
        return await self.session.get(UserStats, user_id)

    async def list_ranked(self) -> list[tuple[User, UserStats]]:
        """Every tallied user, biggest collection first.

        Users with no stats row have never uploaded, so they are absent rather
        than shown as zeroes.
        """
        rows = await self.session.execute(
            select(User, UserStats)
            .join(UserStats, User.id == UserStats.user_id)
            .order_by(UserStats.total_seymours.desc(), User.id)
        )
        return [(user, stats) for user, stats in rows.all()]

    async def totals(self) -> dict:
        row = (
            await self.session.execute(
                select(*[func.coalesce(func.sum(getattr(UserStats, k)), 0) for k in STAT_KEYS])
            )
        ).one()
        return {key: int(value) for key, value in zip(STAT_KEYS, row)}
