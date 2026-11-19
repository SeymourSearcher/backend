"""What the active sync writes, and when it may delete.

Rows are deleted by absence from a complete feed. A failed or empty fetch must
delete nothing: the old age-based sweep, run on its own timer, emptied the
table after a run of failed polls.
"""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import ActiveAuction
from app.models.auction import Bid
from app.services import auction_sync
from app.services.auctions import AuctionBid, SeymourAuction

pytestmark = pytest.mark.integration

AN_HOUR_AGO = utcnow() - timedelta(hours=1)


def row(auction_id: str, price: int = 1) -> ActiveAuction:
    return ActiveAuction(
        auction_id=auction_id, seller_uuid="s", price=price, is_bin=False, start_time=0,
        end_time=10, item_uuid=auction_id, hex_color="FF0000", piece=0,
        last_updated=AN_HOUR_AGO,
    )


def listing(auction_id: str, price: int = 1, bids: list[AuctionBid] | None = None) -> SeymourAuction:
    return SeymourAuction(
        auction_id=auction_id, seller="s", price=price, is_bin=False, start_time=0,
        end_time=10, item_uuid=auction_id, hex_color="FF0000", piece=0, bids=bids,
    )


def feed(monkeypatch, *listings: SeymourAuction):
    async def fetch(client):
        return list(listings)

    monkeypatch.setattr(auction_sync, "get_all_auctions", fetch)
    monkeypatch.setattr(auction_sync, "parse_active_auction", lambda a: a)


async def sync(db):
    result = await auction_sync.sync_active_auctions(db, None)
    await db.commit()
    db.expire_all()
    return result


async def rows(db) -> dict[str, ActiveAuction]:
    return {a.auction_id: a for a in await db.scalars(select(ActiveAuction))}


async def test_a_failed_fetch_deletes_nothing(db, monkeypatch):
    db.add(row("old"))
    await db.commit()

    async def timeout(client):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(auction_sync, "get_all_auctions", timeout)

    with pytest.raises(httpx.ReadTimeout):
        await auction_sync.sync_active_auctions(db, None)
    await db.rollback()

    assert set(await rows(db)) == {"old"}


async def test_an_empty_feed_deletes_nothing(db, monkeypatch):
    db.add(row("old"))
    await db.commit()
    feed(monkeypatch)

    _, changes = await sync(db)

    assert set(await rows(db)) == {"old"}
    assert changes == auction_sync.SyncChanges()


async def test_listings_missing_from_a_complete_feed_are_deleted_with_their_bids(db, monkeypatch):
    db.add_all([row("gone"), row("kept")])
    await db.flush()
    db.add(Bid(auction_id="gone", bidder_uuid="b", amount=5, timestamp=1))
    await db.commit()
    feed(monkeypatch, listing("kept"), listing("new"))

    active_ids, changes = await sync(db)

    assert active_ids == {"kept", "new"}
    assert set(await rows(db)) == {"kept", "new"}
    assert list(await db.scalars(select(Bid))) == []
    assert changes.active_changed == 2, "one inserted, one deleted"


async def test_only_changed_listings_are_rewritten(db, monkeypatch):
    db.add_all([row("same", price=100), row("outbid", price=100)])
    await db.commit()
    stored = (await rows(db))["same"].last_updated
    feed(monkeypatch, listing("same", price=100), listing("outbid", price=150))

    _, changes = await sync(db)

    current = await rows(db)
    assert current["same"].last_updated == stored
    assert current["outbid"].price == 150
    assert current["outbid"].last_updated > stored
    assert changes.active_changed == 0, "a price change adds and removes nothing"


async def test_new_bids_are_recorded_once(db, monkeypatch):
    bid = AuctionBid(bidder_uuid="b", amount=150, timestamp=1)
    feed(monkeypatch, listing("a", price=150, bids=[bid]))

    await sync(db)
    await sync(db)

    assert [b.amount for b in await db.scalars(select(Bid))] == [150]


async def test_the_ended_sync_does_not_delete_by_age(db, monkeypatch):
    db.add(row("old"))
    await db.commit()

    async def no_endings(client):
        return {"auctions": []}

    monkeypatch.setattr(auction_sync, "get_auctions_ended", no_endings)

    await auction_sync.sync_ended_auctions(db, None)
    await db.commit()

    assert set(await rows(db)) == {"old"}
