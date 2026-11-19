from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert
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

    async def active_listing_state(self) -> dict[str, tuple[int, int | None]]:
        """(price, end_time) per active auction: the only fields a listing changes."""
        rows = await self.session.execute(
            select(ActiveAuction.auction_id, ActiveAuction.price, ActiveAuction.end_time)
        )
        return {auction_id: (price, end_time) for auction_id, price, end_time in rows}

    async def upsert_active(self, values: list[dict]) -> None:
        stmt = insert(ActiveAuction).values(values)
        await self.session.execute(stmt.on_duplicate_key_update(
            price=stmt.inserted.price,
            end_time=stmt.inserted.end_time,
            last_updated=stmt.inserted.last_updated,
        ))

    async def delete_active(self, auction_ids: set[str]) -> int:
        """Bids go with them via the FK's ON DELETE CASCADE."""
        if not auction_ids:
            return 0
        result = await self.session.execute(
            delete(ActiveAuction).where(ActiveAuction.auction_id.in_(auction_ids))
        )
        return result.rowcount

    async def bid_timestamps(self, auction_ids: list[str]) -> dict[str, set[int]]:
        rows = await self.session.execute(
            select(Bid.auction_id, Bid.timestamp).where(Bid.auction_id.in_(auction_ids))
        )
        by_auction: dict[str, set[int]] = {}
        for auction_id, timestamp in rows:
            by_auction.setdefault(auction_id, set()).add(timestamp)
        return by_auction

    async def insert_bids(self, values: list[dict]) -> None:
        await self.session.execute(insert(Bid).values(values))
