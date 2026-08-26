"""The players table as the store behind username display.

resolve_names upserts with MySQL's ON DUPLICATE KEY UPDATE, so this only
means anything against real MySQL.
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import Player
from app.services import mojang, player

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_upstream(monkeypatch):
    calls = []

    async def fake_fetch(uuid):
        calls.append(uuid)
        return None if uuid.startswith("dead") else f"name-{uuid[:4]}"

    monkeypatch.setattr(mojang, "name_for_uuid", fake_fetch)
    return calls


@pytest.fixture
def bulk(monkeypatch):
    """Stub the bulk endpoint with a name -> uuid map the test controls."""
    def install(live: dict[str, str]):
        calls = []

        async def fake_bulk(names):
            calls.append(list(names))
            return {n.lower(): u for n, u in live.items()}

        monkeypatch.setattr(mojang, "uuids_for_names", fake_bulk)
        return calls

    return install


class TestLookupNames:
    """The read path: SELECT only, at any age."""

    async def test_serves_a_stored_name(self, db, no_upstream):
        db.add(Player(uuid="a" * 32, username="cached", last_updated=utcnow()))
        await db.commit()

        assert await player.lookup_names(db, ["a" * 32]) == {"a" * 32: "cached"}
        assert no_upstream == []

    async def test_serves_names_well_past_the_refresh_age(self, db, no_upstream):
        uuid = "b" * 32
        db.add(Player(uuid=uuid, username="ancient",
                      last_updated=utcnow() - player.REFRESH_AFTER - timedelta(days=400)))
        await db.commit()

        assert await player.lookup_names(db, [uuid]) == {uuid: "ancient"}
        assert no_upstream == []

    async def test_an_unknown_uuid_is_absent_and_not_fetched(self, db, no_upstream):
        assert await player.lookup_names(db, ["c" * 32]) == {}
        assert no_upstream == []
        assert await db.scalar(select(func.count()).select_from(Player)) == 0

    async def test_dashed_uuids_map_back_to_the_caller_form(self, db):
        dashed = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        db.add(Player(uuid="d" * 32, username="dashed", last_updated=utcnow()))
        await db.commit()

        assert await player.lookup_names(db, [dashed]) == {dashed: "dashed"}

    async def test_empty_and_blank_input(self, db, no_upstream):
        assert await player.lookup_names(db, []) == {}
        assert await player.lookup_names(db, ["", None]) == {}
        assert no_upstream == []


class TestResolveNames:
    """The worker path: fetches and persists."""

    async def test_an_unknown_uuid_is_fetched_and_stored(self, db, no_upstream):
        uuid = "1" * 32

        assert await player.resolve_names(db, [uuid]) == {uuid: "name-1111"}
        assert no_upstream == [uuid]
        assert await db.scalar(select(Player.username).where(Player.uuid == uuid)) == "name-1111"

    async def test_stale_rows_are_refetched_and_updated_in_place(self, db, no_upstream):
        uuid = "2" * 32
        db.add(Player(uuid=uuid, username="old",
                      last_updated=utcnow() - player.REFRESH_AFTER - timedelta(days=1)))
        await db.commit()

        assert await player.resolve_names(db, [uuid]) == {uuid: "name-2222"}
        assert no_upstream == [uuid]
        # Upsert, not insert: one row per uuid.
        assert await db.scalar(select(func.count()).select_from(Player)) == 1
        assert await db.scalar(select(Player.username).where(Player.uuid == uuid)) == "name-2222"

    async def test_fresh_rows_are_left_alone(self, db, no_upstream):
        uuid = "3" * 32
        db.add(Player(uuid=uuid, username="fresh", last_updated=utcnow()))
        await db.commit()

        assert await player.resolve_names(db, [uuid]) == {uuid: "fresh"}
        assert no_upstream == []

    async def test_a_failed_lookup_falls_back_to_the_stale_row(self, db, no_upstream):
        uuid = "dead" + "d" * 28
        db.add(Player(uuid=uuid, username="last-known",
                      last_updated=utcnow() - player.REFRESH_AFTER - timedelta(days=1)))
        await db.commit()

        assert await player.resolve_names(db, [uuid]) == {uuid: "last-known"}

    async def test_a_failed_lookup_with_no_row_is_simply_absent(self, db, no_upstream):
        uuid = "dead" + "e" * 28

        assert await player.resolve_names(db, [uuid]) == {}
        assert await db.scalar(select(func.count()).select_from(Player)) == 0

    async def test_mixed_batch_only_fetches_what_it_must(self, db, no_upstream):
        fresh, stale = "4" * 32, "5" * 32
        db.add_all([
            Player(uuid=fresh, username="fresh", last_updated=utcnow()),
            Player(uuid=stale, username="stale",
                   last_updated=utcnow() - player.REFRESH_AFTER - timedelta(days=1)),
        ])
        await db.commit()

        names = await player.resolve_names(db, [fresh, stale, "6" * 32])

        assert names[fresh] == "fresh"
        assert names[stale] == "name-5555"
        assert names["6" * 32] == "name-6666"
        assert sorted(no_upstream) == sorted([stale, "6" * 32])


class TestRevalidateNames:
    """The bulk path: proves a stored name is still current without a per-uuid call."""

    def _stale(self, uuid, username):
        return Player(uuid=uuid, username=username,
                      last_updated=utcnow() - player.REFRESH_AFTER - timedelta(days=1))

    async def test_a_name_still_on_its_uuid_is_touched_not_refetched(self, db, bulk, no_upstream):
        uuid = "7" * 32
        db.add(self._stale(uuid, "Steady"))
        await db.commit()
        calls = bulk({"Steady": uuid})

        assert await player.revalidate_names(db, [(uuid, "Steady")]) == (1, 0)
        assert calls == [["Steady"]]
        assert no_upstream == []

        row = await db.scalar(select(Player).where(Player.uuid == uuid))
        await db.refresh(row)
        assert row.username == "Steady"
        assert utcnow() - row.last_updated < player.REFRESH_AFTER

    async def test_casing_changes_still_count_as_the_same_name(self, db, bulk, no_upstream):
        uuid = "8" * 32
        db.add(self._stale(uuid, "mixedcase"))
        await db.commit()
        bulk({"MiXeDcAsE": uuid})

        assert await player.revalidate_names(db, [(uuid, "mixedcase")]) == (1, 0)
        assert no_upstream == []

    async def test_a_name_that_moved_to_another_uuid_is_refetched(self, db, bulk, no_upstream):
        uuid = "9" * 32
        db.add(self._stale(uuid, "Taken"))
        await db.commit()
        bulk({"Taken": "f" * 32})  # someone else holds it now

        assert await player.revalidate_names(db, [(uuid, "Taken")]) == (0, 1)
        assert no_upstream == [uuid]
        assert await db.scalar(select(Player.username).where(Player.uuid == uuid)) == "name-9999"

    async def test_a_name_no_longer_registered_is_refetched(self, db, bulk, no_upstream):
        uuid = "a1" + "a" * 30
        db.add(self._stale(uuid, "Renamed"))
        await db.commit()
        bulk({})  # absent from the bulk response entirely

        assert await player.revalidate_names(db, [(uuid, "Renamed")]) == (0, 1)
        assert no_upstream == [uuid]

    async def test_only_the_changed_names_cost_a_request(self, db, bulk, no_upstream):
        same, moved = "b1" + "b" * 30, "c1" + "c" * 30
        db.add_all([self._stale(same, "Same"), self._stale(moved, "Moved")])
        await db.commit()
        bulk({"Same": same, "Moved": "e" * 32})

        confirmed, changed = await player.revalidate_names(
            db, [(same, "Same"), (moved, "Moved")]
        )

        assert (confirmed, changed) == (1, 1)
        assert no_upstream == [moved]

    async def test_nothing_to_revalidate_makes_no_calls(self, db, bulk, no_upstream):
        calls = bulk({})
        assert await player.revalidate_names(db, []) == (0, 0)
        assert calls == []
        assert no_upstream == []
