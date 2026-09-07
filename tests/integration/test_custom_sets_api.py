"""GET /stats/custom-sets, the homepage featured-sets gallery.

The rows are written by refresh_user_stats, so the fixtures seed and tally
exactly as the upload path does. What is worth pinning is the read: the gallery
orders across every user but takes at most GALLERY_PER_USER from any one of
them, and that cap is a predicate on the rank assigned at write time rather than
a window function -- so it only holds if the ranks are stored best-first.
"""
import pytest

from app.models import Seymour, User, UserCustomSet
from app.pieces import Piece
from app.services import custom_sets
from app.services.rankings import refresh_user_stats

pytestmark = pytest.mark.integration


def tight_set(base: int) -> list[str]:
    """Four hexes within a hair of each other -- one qualifying custom set."""
    return [f"{base + offset:06X}" for offset in (0x000000, 0x000001, 0x000100, 0x000101)]


def add_pieces(db, user_id: int, hexes: list[str]) -> None:
    for piece, hex_color in enumerate(hexes):
        db.add(Seymour(user_id=user_id, item_name=Piece(piece), hex_color=hex_color))


async def seed(db, owner, bases: list[int]) -> None:
    for base in bases:
        add_pieces(db, owner.id, tight_set(base))
    await db.commit()
    await refresh_user_stats(db, owner.id)


@pytest.fixture
async def collectors(db, user):
    """One user holding three qualifying sets, another holding one."""
    other = User(discord_id=99, username="rival", avatar_hash="abc")
    db.add(other)
    await db.flush()

    # Greys, tans and greens for the first owner; a red set for the second.
    await seed(db, user, [0x404040, 0xC08040, 0x408040])
    await seed(db, other, [0xFF0000])
    return other


async def test_a_tallied_set_appears_in_the_gallery(client, collectors):
    body = (await client.get("/stats/custom-sets")).json()

    assert body, "seeded inventories produce no sets"
    for tile in body:
        assert set(tile) == {
            "discord_id", "username", "avatar_hash", "avg_delta_e", "hexes"
        }
        assert len(tile["hexes"]) == 4
        assert isinstance(tile["discord_id"], str)


async def test_the_gallery_is_ordered_by_delta_e(client, collectors):
    deltas = [t["avg_delta_e"] for t in (await client.get("/stats/custom-sets")).json()]

    assert deltas == sorted(deltas)


async def test_no_collector_takes_more_than_the_per_user_cap(client, collectors):
    body = (await client.get("/stats/custom-sets")).json()

    counts = {}
    for tile in body:
        counts[tile["username"]] = counts.get(tile["username"], 0) + 1
    assert max(counts.values()) <= custom_sets.GALLERY_PER_USER


async def test_the_cap_leaves_room_for_other_collectors(client, collectors):
    """The whole point of the cap: the owner of three sets cannot fill the row.

    Only meaningful because "tester" really does hold more sets than the cap --
    test_only_the_best_sets_per_user_are_stored pins that.
    """
    body = (await client.get("/stats/custom-sets")).json()

    owners = [t["username"] for t in body]
    assert owners.count("tester") == custom_sets.GALLERY_PER_USER
    assert "rival" in owners


async def test_only_the_best_sets_per_user_are_stored(db, collectors, user):
    stored = (
        await db.execute(
            UserCustomSet.__table__.select().where(UserCustomSet.user_id == user.id)
        )
    ).all()

    # Three seeded sets, all qualifying, so the SETS_PER_USER cap is exercised
    # rather than merely satisfied.
    assert len(stored) == 3
    assert sorted(row.set_rank for row in stored) == [0, 1, 2]

    by_rank = sorted(stored, key=lambda row: row.set_rank)
    assert [row.avg_delta_e for row in by_rank] == sorted(row.avg_delta_e for row in by_rank)


async def test_deleting_a_collection_clears_its_sets(client, as_user, user, collectors):
    as_user(user)
    assert (await client.delete("/seymours/")).status_code == 200

    body = (await client.get("/stats/custom-sets")).json()
    assert {t["username"] for t in body} == {"rival"}


async def test_the_gallery_is_empty_when_nobody_has_uploaded(client):
    assert (await client.get("/stats/custom-sets")).json() == []


async def test_an_oversized_inventory_is_left_out_rather_than_searched(
    client, db, user, collectors, monkeypatch
):
    """The ceiling exists because the search runs inside upload and delete
    requests and grows superlinearly -- see MAX_INVENTORY."""
    monkeypatch.setattr("app.services.custom_sets.MAX_INVENTORY", 4)

    def explode(*_args, **_kwargs):
        raise AssertionError("searched an inventory over the ceiling")

    monkeypatch.setattr("app.services.custom_sets.find_custom_sets", explode)
    await refresh_user_stats(db, user.id)

    # Cleared, not merely skipped: the previously stored rows are gone.
    assert {t["username"] for t in (await client.get("/stats/custom-sets")).json()} == {"rival"}


async def test_a_loose_inventory_produces_no_sets(client, db, user):
    """Four wildly different colours are not a set, however few pieces exist."""
    add_pieces(db, user.id, ["FF0000", "00FF00", "0000FF", "FFFFFF"])
    await db.commit()
    await refresh_user_stats(db, user.id)

    assert (await client.get("/stats/custom-sets")).json() == []
