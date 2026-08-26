from datetime import datetime

from sqlalchemy import select, union, update
from sqlalchemy.dialects.mysql import insert

from app.models import ActiveAuction, EndedAuction, Player
from app.models.auction import Bid
from app.repositories.base import BaseRepository

# The refresh worker only needs uuids new since its last pass, so it looks at a
# window of recent ended auctions rather than all of them. The historical
# backlog is a one-off job, not this query's problem.
#
# ~2 days of auctions against a 60s cycle. Sized to stay a backward index scan:
# widening it past a few thousand rows tips the optimizer into a full scan.
RECENT_ENDED = 2_000


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

    async def unresolved_uuids(self, limit: int) -> list[str]:
        """Recently-seen uuids with no players row at all.

        A CTE, not a subquery: seller and buyer both read the recent window, and
        inlining it makes MySQL scan ended_auctions twice instead of once.
        """
        recent = (
            select(
                EndedAuction.seller_uuid.label("seller"),
                EndedAuction.buyer_uuid.label("buyer"),
            )
            .order_by(EndedAuction.timestamp.desc())
            .limit(RECENT_ENDED)
            .cte("recent_ended")
        )
        seen = union(
            select(recent.c.seller.label("u")),
            select(recent.c.buyer.label("u")),
            select(ActiveAuction.seller_uuid.label("u")),
            select(Bid.bidder_uuid.label("u")),
        ).subquery()

        rows = await self.session.execute(
            select(seen.c.u)
            .outerjoin(Player, Player.uuid == seen.c.u)
            .where(Player.uuid.is_(None), seen.c.u.isnot(None), seen.c.u != "")
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def touch(self, uuids: list[str], now: datetime) -> None:
        """Mark names as re-verified. The username is unchanged, so don't rewrite it."""
        await self.session.execute(
            update(Player).where(Player.uuid.in_(uuids)).values(last_updated=now)
        )

    async def stale_players(self, limit: int, cutoff: datetime) -> list[tuple[str, str]]:
        """(uuid, username) past their refresh age, oldest first.

        The username comes back because revalidating in bulk needs it as the
        query key -- see player.revalidate_names.
        """
        rows = await self.session.execute(
            select(Player.uuid, Player.username)
            .where(Player.last_updated < cutoff)
            .order_by(Player.last_updated)
            .limit(limit)
        )
        return [(uuid, username) for uuid, username in rows.all()]
