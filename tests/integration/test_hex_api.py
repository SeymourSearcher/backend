"""GET /hex/{hex_code}.

Had no coverage: neither the endpoint, nor HexInfoResponse, nor the tiering pass
that walks every user's inventory. FFFF00 is an exact Farm Suit colour, so the
top match is deterministic.
"""
import pytest

from app.models import Seymour, User
from app.pieces import Piece

pytestmark = pytest.mark.integration

TARGET = "FFFF00"


@pytest.fixture
async def owners(db):
    """One user holding the exact colour, one holding a near miss, one far off."""
    rows = [
        (User(discord_id=11, username="exact", avatar_hash=None), TARGET),
        (User(discord_id=12, username="close", avatar_hash=None), "FFFE00"),
        (User(discord_id=13, username="far", avatar_hash=None), "000000"),
    ]
    for user, hex_color in rows:
        db.add(user)
        await db.flush()
        db.add(Seymour(user_id=user.id, item_name=Piece.HELMET, hex_color=hex_color))
    await db.commit()


async def test_returns_the_exact_set_as_the_top_match(client, owners):
    body = (await client.get(f"/hex/{TARGET}")).json()

    assert body["hex_code"] == TARGET
    top = body["top_matches"][0]
    assert top["target_hex"] == TARGET
    assert top["target_name"] == "Farm Suit"
    assert top["delta_e"] == 0.0
    assert top["pieces"] == [0, 1, 2, 3]


async def test_pieces_and_item_names_serialise_as_indices(client, owners):
    body = (await client.get(f"/hex/{TARGET}")).json()

    for match in body["top_matches"]:
        assert all(isinstance(p, int) for p in match["pieces"])


async def test_owners_are_bucketed_by_tier_best_first(client, owners):
    top = (await client.get(f"/hex/{TARGET}")).json()["top_matches"][0]
    tiers = top["users_by_tier"]

    assert [u["username"] for u in tiers["t1"]] == ["exact", "close"]
    assert [u["delta_e"] for u in tiers["t1"]] == sorted(u["delta_e"] for u in tiers["t1"])
    assert tiers["t1"][0]["delta_e"] == 0.0

    everyone = [u["username"] for tier in tiers.values() for u in tier]
    assert "far" not in everyone


async def test_discord_id_is_serialised_as_a_string(client, owners):
    top = (await client.get(f"/hex/{TARGET}")).json()["top_matches"][0]
    assert top["users_by_tier"]["t1"][0]["discord_id"] == "11"


@pytest.mark.parametrize("bad", ["ZZZZZZ", "12345", "1234567", "", "nothex"])
async def test_malformed_hex_is_rejected(client, bad):
    assert (await client.get(f"/hex/{bad}")).status_code in (400, 404)


async def test_lowercase_and_hash_prefixed_input_are_normalised(client, owners):
    lower = (await client.get(f"/hex/{TARGET.lower()}")).json()
    assert lower["hex_code"] == TARGET


async def test_second_call_is_served_from_cache(client, owners, db):
    first = (await client.get(f"/hex/{TARGET}")).json()

    await db.execute(Seymour.__table__.delete())
    await db.commit()

    assert (await client.get(f"/hex/{TARGET}")).json() == first
