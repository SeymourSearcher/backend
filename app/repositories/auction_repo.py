from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import ActiveAuction, EndedAuction
from app.models.auction import Bid
from app.repositories.base import BaseRepository


class AuctionRepository(BaseRepository):
    async def get_active(self, auction_id: str) -> ActiveAuction | None:
        return (
            await self.session.execute(
                select(ActiveAuction)
                .options(selectinload(ActiveAuction.bids))
                .where(ActiveAuction.auction_id == auction_id)
            )
        ).scalar_one_or_none()

    async def get_ended(self, auction_id: str) -> EndedAuction | None:
        return (
            await self.session.execute(
                select(EndedAuction).where(EndedAuction.auction_id == auction_id)
            )
        ).scalar_one_or_none()

    async def recent_bids(self, limit: int) -> list[tuple[Bid, ActiveAuction]]:
        rows = await self.session.execute(
            select(Bid, ActiveAuction)
            .join(ActiveAuction, Bid.auction_id == ActiveAuction.auction_id)
            .order_by(Bid.timestamp.desc())
            .limit(limit)
        )
        return list(rows.all())
