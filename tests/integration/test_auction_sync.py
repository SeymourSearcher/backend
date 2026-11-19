"""When active auctions are swept, and when they must not be.

The sweep used to run from the ended sync on its own timer, so a run of failed
active polls aged every row past the threshold and emptied the table.
"""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import ActiveAuction
from app.services import auction_sync

pytestmark = pytest.mark.integration

AGED = timedelta(minutes=auction_sync.STALE_THRESHOLD_MINUTES + 1)


def active(auction_id: str, age: timedelta = timedelta()) -> ActiveAuction:
    return ActiveAuction(
        auction_id=auction_id, seller_uuid="s", price=1, is_bin=True, start_time=0,
        end_time=None, item_uuid=auction_id, hex_color="FF0000", piece=0,
        last_updated=utcnow() - age,
    )


async def remaining(db) -> set[str]:
    return set(await db.scalars(select(ActiveAuction.auction_id)))


async def test_a_failed_fetch_sweeps_nothing(db, monkeypatch):
    db.add(active("old", AGED))
    await db.commit()

    async def timeout():
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(auction_sync, "get_all_auctions", timeout)

    with pytest.raises(httpx.ReadTimeout):
        await auction_sync.sync_active_auctions(db)
    await db.rollback()

    assert await remaining(db) == {"old"}


async def test_a_complete_fetch_sweeps_rows_it_did_not_refresh(db, monkeypatch):
    db.add_all([active("gone", AGED), active("recent")])
    await db.commit()

    async def empty_feed():
        return []

    monkeypatch.setattr(auction_sync, "get_all_auctions", empty_feed)

    _, changes = await auction_sync.sync_active_auctions(db)
    await db.commit()

    assert await remaining(db) == {"recent"}
    assert changes.active_changed == 1


async def test_the_ended_sync_no_longer_sweeps_by_age(db, monkeypatch):
    db.add(active("old", AGED))
    await db.commit()

    async def no_endings():
        return {"auctions": []}

    monkeypatch.setattr(auction_sync, "get_auctions_ended", no_endings)

    await auction_sync.sync_ended_auctions(db)
    await db.commit()

    assert await remaining(db) == {"old"}
