"""The players table as a cache.

get_player_names upserts with MySQL's ON DUPLICATE KEY UPDATE, so this only
means anything against real MySQL.
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import Player
from app.services import player

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_upstream(monkeypatch):
    calls = []

    async def fake_fetch(uuid):
        calls.append(uuid)
        return None if uuid.startswith("dead") else f"name-{uuid[:4]}"

    monkeypatch.setattr(player, "fetch_username_from_api", fake_fetch)
    return calls


async def test_fresh_rows_are_served_without_calling_upstream(db, no_upstream):
    db.add(Player(uuid="a" * 32, username="cached", last_updated=utcnow()))
    await db.commit()

    names = await player.get_player_names(db, ["a" * 32])

    assert names == {"a" * 32: "cached"}
    assert no_upstream == []


async def test_stale_rows_are_refetched_and_updated_in_place(db, no_upstream):
    uuid = "b" * 32
    db.add(Player(uuid=uuid, username="old",
                  last_updated=utcnow() - player.CACHE_DURATION - timedelta(days=1)))
    await db.commit()

    names = await player.get_player_names(db, [uuid])

    assert names == {uuid: "name-bbbb"}
    assert no_upstream == [uuid]
    # Upsert, not insert: one row per uuid.
    assert await db.scalar(select(func.count()).select_from(Player)) == 1
    assert await db.scalar(select(Player.username).where(Player.uuid == uuid)) == "name-bbbb"


async def test_dashed_uuids_map_back_to_the_caller_form(db, no_upstream):
    dashed = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    names = await player.get_player_names(db, [dashed])

    assert names == {dashed: "name-cccc"}
    assert await db.scalar(select(Player.uuid)) == "c" * 32


async def test_a_failed_lookup_falls_back_to_the_stale_row(db, no_upstream):
    uuid = "dead" + "d" * 28
    db.add(Player(uuid=uuid, username="last-known",
                  last_updated=utcnow() - player.CACHE_DURATION - timedelta(days=1)))
    await db.commit()

    names = await player.get_player_names(db, [uuid])

    assert names == {uuid: "last-known"}


async def test_a_failed_lookup_with_no_row_is_simply_absent(db, no_upstream):
    uuid = "dead" + "e" * 28

    assert await player.get_player_names(db, [uuid]) == {}
    assert await db.scalar(select(func.count()).select_from(Player)) == 0


async def test_empty_and_blank_input(db, no_upstream):
    assert await player.get_player_names(db, []) == {}
    assert await player.get_player_names(db, ["", None]) == {}
    assert no_upstream == []


async def test_mixed_batch_only_fetches_the_stale_ones(db, no_upstream):
    fresh, stale = "1" * 32, "2" * 32
    db.add_all([
        Player(uuid=fresh, username="fresh", last_updated=utcnow()),
        Player(uuid=stale, username="stale",
               last_updated=utcnow() - player.CACHE_DURATION - timedelta(days=1)),
    ])
    await db.commit()

    names = await player.get_player_names(db, [fresh, stale, "3" * 32])

    assert names[fresh] == "fresh"
    assert names[stale] == "name-2222"
    assert names["3" * 32] == "name-3333"
    assert sorted(no_upstream) == sorted([stale, "3" * 32])
