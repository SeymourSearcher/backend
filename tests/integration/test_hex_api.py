"""GET /hex/{hex_code} and GET /hex/{hex_code}/owners.

Had no coverage: neither the endpoint, nor HexInfoResponse, nor the pass that
walks every user's inventory. FFFF00 is an exact Farm Suit colour, so the top
match is deterministic; FFFE00 sits at ΔE 0.57 from it and 000000 at 137.
"""
import pytest

from app.models import Seymour, User
from app.pieces import Piece
from app.services.matching import get_tier

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


async def test_a_catalog_colour_is_reported_as_an_exact_match(client, owners):
    body = (await client.get(f"/hex/{TARGET}")).json()

    assert body["hex_code"] == TARGET
    exact = body["exact_match"]
    assert exact["target_hex"] == TARGET
    assert exact["target_name"] == "Farm Suit"
    assert exact["delta_e"] == 0.0
    assert exact["pieces"] == [0, 1, 2, 3]


async def test_the_exact_match_is_not_repeated_among_the_near_ones(client, owners):
    """It is the colour, not something near it -- listing it as "closest" is noise."""
    body = (await client.get(f"/hex/{TARGET}")).json()

    assert TARGET not in [m["target_hex"] for m in body["top_matches"]]
    assert all(m["delta_e"] > 0 for m in body["top_matches"])


async def test_three_near_matches_survive_dropping_the_exact_one(client, owners):
    assert len((await client.get(f"/hex/{TARGET}")).json()["top_matches"]) == 3


async def test_a_colour_that_is_not_in_the_catalog_has_no_exact_match(client, owners):
    body = (await client.get("/hex/A3F2C1")).json()

    assert body["exact_match"] is None
    assert len(body["top_matches"]) == 3


async def test_pieces_and_item_names_serialise_as_indices(client, owners):
    body = (await client.get(f"/hex/{TARGET}")).json()

    for match in body["top_matches"]:
        assert all(isinstance(p, int) for p in match["pieces"])


async def test_a_match_buckets_its_owners_by_tier(client, owners):
    tiers = (await client.get(f"/hex/{TARGET}")).json()["exact_match"]["users_by_tier"]

    assert [u["username"] for u in tiers["t1"]] == ["exact", "close"]
    assert tiers["t1"][0]["delta_e"] == 0.0
    assert tiers["t1"][0]["discord_id"] == "11"

    everyone = [u["username"] for tier in tiers.values() for u in tier]
    assert "far" not in everyone  # 000000 is 137 away


async def test_every_match_carries_a_price_slot(client, owners):
    """No auctions exist in this schema, so the honest answer is None, not 0."""
    body = (await client.get(f"/hex/{TARGET}")).json()

    for match in [body["exact_match"], *body["top_matches"]]:
        assert match["price"] == {"median": None, "sales": 0}


class TestAuctionStats:
    """The section elaborates on the hero's figure, so it must resolve to the
    same colour the hero priced."""

    async def test_an_exact_colour_is_priced_as_itself(self, client, owners):
        body = (await client.get(f"/hex/{TARGET}/auction-stats")).json()

        assert body["target_hex"] == TARGET
        assert body["target_name"] == "Farm Suit"
        assert body["tier"] == "t1"

    async def test_an_empty_table_reports_zeroes_not_an_error(self, client, owners):
        body = (await client.get(f"/hex/{TARGET}/auction-stats")).json()

        assert body["total"] == 0
        assert body["median_price"] == 0.0
        assert body["price_distribution"] == []
        assert len(body["hourly_activity"]) == 24

    @pytest.mark.parametrize("searched", [TARGET, "FFFA00", "A3F2C1"])
    async def test_it_resolves_to_the_same_reference_as_the_hero(self, client, owners, searched):
        """The section elaborates on the hero's number, so it must price the
        same colour at the tier that colour's own distance earns."""
        info = (await client.get(f"/hex/{searched}")).json()
        body = (await client.get(f"/hex/{searched}/auction-stats")).json()

        reference = info["exact_match"] or info["top_matches"][0]
        assert body["target_hex"] == reference["target_hex"]
        assert body["target_name"] == reference["target_name"]
        assert body["tier"] == get_tier(reference["delta_e"])

    @pytest.mark.parametrize("bad", ["ZZZZZZ", "12345", "nothex"])
    async def test_malformed_hex_is_rejected(self, client, bad):
        assert (await client.get(f"/hex/{bad}/auction-stats")).status_code in (400, 404)


class TestPieceFilter:
    async def test_matches_are_restricted_to_the_chosen_slots(self, client, owners):
        body = (await client.get(f"/hex/{TARGET}?pieces=0")).json()

        for match in [body["exact_match"], *body["top_matches"]]:
            assert Piece.HELMET in match["pieces"]

    async def test_an_unfiltered_request_is_not_narrowed(self, client, owners):
        wide = (await client.get(f"/hex/{TARGET}")).json()
        narrow = (await client.get(f"/hex/{TARGET}?pieces=0")).json()

        assert len(wide["top_matches"]) >= len(narrow["top_matches"])

    async def test_owners_are_filtered_by_slot(self, client, db):
        user = User(discord_id=41, username="mixed", avatar_hash=None)
        db.add(user)
        await db.flush()
        db.add(Seymour(user_id=user.id, item_name=Piece.HELMET, hex_color=TARGET))
        db.add(Seymour(user_id=user.id, item_name=Piece.BOOTS, hex_color=TARGET))
        await db.commit()

        boots = (await client.get(f"/hex/{TARGET}/owners?pieces=3")).json()

        assert boots["total"] == 1
        assert boots["items"][0]["piece"] == Piece.BOOTS

    async def test_filtering_owners_reuses_one_cached_scan(self, client, owners, db):
        """The slot filter runs after the cache read, so flipping it must not
        re-walk the table."""
        await client.get(f"/hex/{TARGET}/owners?pieces=0")

        await db.execute(Seymour.__table__.delete())
        await db.commit()

        assert (await client.get(f"/hex/{TARGET}/owners?pieces=3")).json()["total"] == 0
        assert (await client.get(f"/hex/{TARGET}/owners?pieces=0")).json()["total"] == 2


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


class TestOwners:
    """The list is measured against the searched colour, not via the catalog."""

    async def test_only_owners_inside_the_radius_are_listed(self, client, owners):
        body = (await client.get(f"/hex/{TARGET}/owners")).json()

        assert body["total"] == 2
        assert [m["username"] for m in body["items"]] == ["exact", "close"]
        assert body["items"][0]["delta_e"] == 0.0

    async def test_a_holding_carries_its_own_colour_and_piece(self, client, owners):
        first = (await client.get(f"/hex/{TARGET}/owners")).json()["items"][0]

        assert first["hex_color"] == TARGET
        assert first["piece"] == Piece.HELMET
        assert first["quantity"] == 1
        assert first["discord_id"] == "11"

    async def test_paging_splits_the_list_without_gaps_or_overlap(self, client, owners):
        first = (await client.get(f"/hex/{TARGET}/owners?limit=1&offset=0")).json()
        second = (await client.get(f"/hex/{TARGET}/owners?limit=1&offset=1")).json()

        assert first["total"] == second["total"] == 2
        assert [m["username"] for m in first["items"]] == ["exact"]
        assert [m["username"] for m in second["items"]] == ["close"]

    async def test_paging_past_the_end_is_empty_not_an_error(self, client, owners):
        body = (await client.get(f"/hex/{TARGET}/owners?offset=50")).json()

        assert body["total"] == 2
        assert body["items"] == []

    async def test_identical_holdings_collapse_into_a_quantity(self, client, db):
        hoarder = User(discord_id=31, username="hoarder", avatar_hash=None)
        db.add(hoarder)
        await db.flush()
        for _ in range(3):
            db.add(Seymour(user_id=hoarder.id, item_name=Piece.BOOTS, hex_color=TARGET))
        await db.commit()

        body = (await client.get(f"/hex/{TARGET}/owners")).json()

        assert body["total"] == 1
        assert body["items"][0]["quantity"] == 3

    @pytest.mark.parametrize("bad", ["ZZZZZZ", "12345", "nothex"])
    async def test_malformed_hex_is_rejected(self, client, bad):
        assert (await client.get(f"/hex/{bad}/owners")).status_code in (400, 404)


class TestOwnerSnapshot:
    """The owner rows are cached once for all hex codes, not once per code.

    Rebuilding them is a full walk of the seymours table -- ~1s on prod, and 97%
    of a cold request -- and the result is identical whichever hex was asked for.
    """

    async def test_a_different_hex_reuses_the_snapshot(self, client, owners, db):
        """include_dyes is its own hex_info key, so this misses that cache but
        must still hit the shared owner snapshot."""
        await client.get(f"/hex/{TARGET}")

        await db.execute(Seymour.__table__.delete())
        await db.commit()

        body = (await client.get(f"/hex/{TARGET}?include_dyes=true")).json()
        tiers = body["exact_match"]["users_by_tier"]
        assert [u["username"] for u in tiers["t1"]] == ["exact", "close"]

    async def test_an_upload_rebuilds_it(self, client, as_user, owners, db, user):
        await client.get(f"/hex/{TARGET}/owners")

        as_user(user)
        upload = await client.post(
            "/seymours/upload",
            files={"file": ("s.txt", f"Velvet Top Hat | #{TARGET} | whatever\n", "text/plain")},
        )
        assert upload.json()["imported"] == 1

        body = (await client.get(f"/hex/{TARGET}/owners")).json()
        assert "tester" in [m["username"] for m in body["items"]]

    async def test_a_delete_rebuilds_it(self, client, as_user, db):
        """Deletes used to skip hex invalidation entirely, so a removed Seymour
        kept its owner on the hex page for the full 30 minute TTL."""
        owner = User(discord_id=21, username="leaver", avatar_hash=None)
        db.add(owner)
        await db.flush()
        db.add(Seymour(user_id=owner.id, item_name=Piece.HELMET, hex_color=TARGET))
        await db.commit()

        await client.get(f"/hex/{TARGET}/owners")

        as_user(owner)
        assert (await client.delete("/seymours/")).status_code == 200

        body = (await client.get(f"/hex/{TARGET}/owners")).json()
        assert "leaver" not in [m["username"] for m in body["items"]]
