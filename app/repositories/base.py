from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select


class BaseRepository:
    """Repos take a live session, flush but never commit — the owning service commits."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _total(self, query: Select) -> int:
        """Row count for a query, ignoring its ordering/pagination."""
        return await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0

    @staticmethod
    def _paginate(query: Select, skip: int, limit: int) -> Select:
        return query.offset(skip).limit(limit)
