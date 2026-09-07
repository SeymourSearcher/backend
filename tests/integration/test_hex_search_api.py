"""GET /hex-search.

Results are colours somebody in the database holds, so every case here seeds
holdings first. FFFF00 is an exact Pure Yellow catalog colour, which makes the
top row and its tier deterministic.
"""
import pytest

from app.models import Seymour, User
from app.pieces import Piece

pytestmark = pytest.mark.integration

PATTERN = "FFFF0?"


async def hold(db, discord_id, username, holdings):
    """holdings are (hex_color, piece); returns the created user."""
    user = User(discord_id=discord_id, username=username, avatar_hash=None)
    db.add(user)
    await db.flush()
    for hex_color, piece in holdings:
        db.add(Seymour(user_id=user.id, item_name=piece, hex_color=hex_color))
    await db.commit()
    return user


@pytest.fixture
async def me(db):
    return await hold(db, 21, "me", [("FFFF00", Piece.HELMET), ("FFFF00", Piece.BOOTS)])


@pytest.fixture
async def them(db):
    return await hold(db, 22, "them", [("FFFF05", Piece.HELMET)])


def codes(body):
    return {row["hex_code"] for row in body["items"]}


async def test_only_held_colours_come_back(client, me, them):
    response = await client.get(f"/hex-search?p={PATTERN}")

    assert response.status_code == 200
    body = response.json()
    assert body["pattern"] == PATTERN
    assert body["total"] == 2
    assert codes(body) == {"FFFF00", "FFFF05"}


async def test_the_pattern_total_still_spans_the_colour_space(client, me):
    """The gap against `total` is what says how rare the pattern is."""
    body = (await client.get(f"/hex-search?p={PATTERN}")).json()

    assert (body["total"], body["pattern_total"]) == (1, 16)


async def test_a_pattern_nobody_holds_is_empty(client, me):
    body = (await client.get("/hex-search?p=21????")).json()

    assert body["total"] == 0 and body["items"] == []


async def test_rows_carry_their_catalog_match_and_tier(client, me):
    row = (await client.get(f"/hex-search?p={PATTERN}")).json()["items"][0]

    assert row["hex_code"] == "FFFF00"
    assert row["target_hex"] == "FFFF00"
    assert row["delta_e"] == 0.0
    assert row["tier"] == "t1"


async def test_every_row_names_who_holds_it(client, me):
    row = (await client.get(f"/hex-search?p={PATTERN}")).json()["items"][0]

    assert row["owner_count"] == 1
    owner = row["owners"][0]
    assert owner["username"] == "me"
    # Serialised as a string by PublicUser, though the column is an integer.
    assert owner["discord_id"] == "21"
    assert sorted(owner["pieces"]) == [Piece.HELMET, Piece.BOOTS] and owner["quantity"] == 2


async def test_rows_are_ordered_by_delta_e(client, me, them):
    body = (await client.get(f"/hex-search?p={PATTERN}")).json()

    des = [row["delta_e"] for row in body["items"]]
    assert des == sorted(des)


class TestMineOnly:
    async def test_it_narrows_to_the_callers_holdings(self, client, as_user, me, them):
        as_user(me)

        body = (await client.get(f"/hex-search?p={PATTERN}&mine_only=true")).json()

        assert body["mine_only"] is True
        assert codes(body) == {"FFFF00"}

    async def test_without_it_everyones_holdings_are_in_scope(self, client, as_user, me, them):
        as_user(me)

        body = (await client.get(f"/hex-search?p={PATTERN}")).json()

        assert body["mine_only"] is False
        assert codes(body) == {"FFFF00", "FFFF05"}

    async def test_an_anonymous_caller_stays_database_wide(self, client, me, them):
        """The toggle is unavailable logged out, so asking for it is ignored
        rather than refused."""
        body = (await client.get(f"/hex-search?p={PATTERN}&mine_only=true")).json()

        assert body["mine_only"] is False
        assert codes(body) == {"FFFF00", "FFFF05"}


class TestOwnedByMe:
    async def test_it_marks_the_callers_own_colours(self, client, as_user, me, them):
        as_user(me)

        body = (await client.get(f"/hex-search?p={PATTERN}")).json()

        assert {r["hex_code"]: r["owned_by_me"] for r in body["items"]} == {
            "FFFF00": True,
            "FFFF05": False,
        }

    async def test_an_anonymous_caller_owns_nothing(self, client, me):
        body = (await client.get(f"/hex-search?p={PATTERN}")).json()

        assert all(row["owned_by_me"] is False for row in body["items"])


async def test_the_piece_filter_only_narrows_the_owners(client, db, me):
    """Someone else holds the same colour on a slot the filter excludes."""
    await hold(db, 23, "bootsfan", [("FFFF00", Piece.BOOTS)])

    body = (await client.get(f"/hex-search?p={PATTERN}&pieces=3")).json()
    row = body["items"][0]

    assert row["delta_e"] == 0.0
    assert {o["username"] for o in row["owners"]} == {"me", "bootsfan"}
    assert all(o["pieces"] == [Piece.BOOTS] for o in row["owners"])


async def test_the_piece_filter_drops_colours_held_only_elsewhere(client, me, them):
    """them holds FFFF05 on a helmet only, so a boots filter loses the colour."""
    body = (await client.get(f"/hex-search?p={PATTERN}&pieces=3")).json()

    assert codes(body) == {"FFFF00"}


@pytest.mark.parametrize("tier", ["t1", "t2", "t3"])
async def test_a_tier_selects_exactly_the_rows_that_earn_it(client, me, them, tier):
    """Derived from the unfiltered page rather than pinned: FFFF00 is an exact
    catalog colour, so its neighbours are all t1 and 'narrows to fewer' would
    assert nothing."""
    unfiltered = (await client.get(f"/hex-search?p={PATTERN}")).json()["items"]
    expected = {row["hex_code"] for row in unfiltered if row["tier"] == tier}

    body = (await client.get(f"/hex-search?p={PATTERN}&tier={tier}")).json()

    assert body["total"] == len(expected)
    assert codes(body) == expected


async def test_paging_is_stable_across_pages(client, db):
    await hold(db, 30, "collector", [(f"FFFF0{d:X}", Piece.HELMET) for d in range(6)])

    first = (await client.get(f"/hex-search?p={PATTERN}&limit=3&offset=0")).json()
    second = (await client.get(f"/hex-search?p={PATTERN}&limit=3&offset=3")).json()
    both = (await client.get(f"/hex-search?p={PATTERN}&limit=6")).json()

    assert [r["hex_code"] for r in first["items"] + second["items"]] == [
        r["hex_code"] for r in both["items"]
    ]


async def test_an_unconstrained_pattern_returns_every_held_colour(client, me, them):
    body = (await client.get("/hex-search?p=??????")).json()

    assert body["pattern_total"] == 0x1000000
    assert codes(body) == {"FFFF00", "FFFF05"}


async def test_the_default_page_is_ten(client, db):
    await hold(db, 31, "collector", [(f"FFFF0{d:X}", Piece.HELMET) for d in range(16)])

    body = (await client.get(f"/hex-search?p={PATTERN}")).json()

    assert body["limit"] == 10
    assert len(body["items"]) == 10 and body["total"] == 16


@pytest.mark.parametrize("raw", ["ZZZZZ", "1234567", "12345!", "nothex!", ""])
async def test_malformed_patterns_are_refused(client, raw):
    response = await client.get("/hex-search", params={"p": raw})

    assert response.status_code in (400, 422)


@pytest.mark.parametrize(
    "query", ["p=21????&tier=t9", "p=21????&limit=200", "p=21????&limit=0", "p=21????&offset=99999"]
)
async def test_out_of_range_parameters_are_refused(client, query):
    assert (await client.get(f"/hex-search?{query}")).status_code == 422


async def test_the_pattern_is_required(client):
    assert (await client.get("/hex-search")).status_code == 422
