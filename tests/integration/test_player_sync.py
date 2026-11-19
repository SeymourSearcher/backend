"""Failed lookups sit out of the refresh queues instead of heading them forever.

Both queues take the first N rows, so a few unresolvable uuids at the front used
to be retried every cycle while everything behind them waited -- the players
table went seven weeks without a single new or refreshed name.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import ActiveAuction, Player, PlayerLookupFailure
from app.repositories.player_repo import PlayerRepository
from app.services import mojang, player

pytestmark = pytest.mark.integration

DEAD = "dead" + "0" * 28
LIVE = "1" * 32
STALE = utcnow() - player.REFRESH_AFTER - timedelta(days=1)


@pytest.fixture(autouse=True)
def upstream(monkeypatch):
    async def fake_fetch(uuid):
        return None if uuid.startswith("dead") else f"name-{uuid[:4]}"

    monkeypatch.setattr(mojang, "name_for_uuid", fake_fetch)


def retry_cutoff():
    return utcnow() - player.RETRY_FAILED_AFTER


async def test_a_failed_refresh_leaves_the_stale_queue(db):
    db.add_all([
        Player(uuid=DEAD, username="Gone", last_updated=STALE),
        Player(uuid=LIVE, username="Here", last_updated=STALE + timedelta(seconds=1)),
    ])
    await db.commit()
    repo = PlayerRepository(db)

    await player.resolve_names(db, [DEAD])

    queued = await repo.stale_players(10, utcnow() - player.REFRESH_AFTER, retry_cutoff())
    assert [uuid for uuid, _ in queued] == [LIVE]


async def test_it_rejoins_the_queue_once_the_retry_window_passes(db):
    db.add_all([
        Player(uuid=DEAD, username="Gone", last_updated=STALE),
        PlayerLookupFailure(uuid=DEAD, failed_at=utcnow() - player.RETRY_FAILED_AFTER * 2),
    ])
    await db.commit()

    queued = await PlayerRepository(db).stale_players(
        10, utcnow() - player.REFRESH_AFTER, retry_cutoff()
    )

    assert [uuid for uuid, _ in queued] == [DEAD]


async def test_a_successful_lookup_clears_the_failure(db):
    db.add(PlayerLookupFailure(uuid=LIVE, failed_at=utcnow() - timedelta(days=1)))
    await db.commit()

    await player.resolve_names(db, [LIVE])

    assert await db.scalar(select(PlayerLookupFailure).where(PlayerLookupFailure.uuid == LIVE)) is None
    assert (await db.get(Player, LIVE)).username == "name-1111"


async def test_a_failed_new_uuid_leaves_the_unresolved_queue(db):
    db.add_all(
        ActiveAuction(
            auction_id=f"a{seller[:4]}", seller_uuid=seller, price=1, is_bin=True,
            start_time=0, end_time=None, item_uuid=f"i{seller[:4]}", hex_color="FF0000",
            piece=0, last_updated=utcnow(),
        )
        for seller in (DEAD, LIVE)
    )
    await db.commit()
    repo = PlayerRepository(db)

    await player.resolve_names(db, [DEAD])

    assert await repo.unresolved_uuids(10, retry_cutoff()) == [LIVE]
