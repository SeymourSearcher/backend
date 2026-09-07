"""GET /hex-search.

FFFF00 is an exact Pure Yellow colour, so a pattern that pins it down has a
deterministic top row. FFFF0? is sixteen colours, small enough that one page
holds all of them and every assertion about ownership actually bites.
"""
import pytest

from app.models import Seymour, User
from app.pieces import Piece

pytestmark = pytest.mark.integration

PATTERN = "FFFF0?"


@pytest.fixture
async def holders(db):
    """One user holding the exact colour on two pieces, one holding a neighbour."""
    rows = [
        (User(discord_id=21, username="exact", avatar_hash=None), "FFFF00", Piece.HELMET),
        (User(discord_id=21, username="exact", avatar_hash=None), "FFFF00", Piece.BOOTS),
        (User(discord_id=22, username="neighbour", avatar_hash=None), "FFFF05", Piece.HELMET),
    ]
    seen: dict[int, int] = {}
    for user, hex_color, piece in rows:
        user_id = seen.get(user.discord_id)
        if user_id is None:
            db.add(user)
            await db.flush()
            user_id = seen[user.discord_id] = user.id
        db.add(Seymour(user_id=user_id, item_name=piece, hex_color=hex_color))
    await db.commit()


async def test_a_pattern_returns_every_colour_it_denotes(client):
    response = await client.get(f"/hex-search?p={PATTERN}")

    assert response.status_code == 200
    body = response.json()
    assert body["pattern"] == PATTERN
    assert body["pattern_total"] == 16
    assert body["total"] == 16
    assert len(body["items"]) == 16
    assert {item["hex_code"] for item in body["items"]} == {f"FFFF0{d:X}" for d in range(16)}


async def test_rows_carry_their_catalog_match_and_tier(client):
    body = (await client.get(f"/hex-search?p={PATTERN}")).json()
    row = next(r for r in body["items"] if r["hex_code"] == "FFFF00")

    assert row["target_hex"] == "FFFF00"
    assert row["delta_e"] == 0.0
    assert row["tier"] == "t1"


async def test_rows_are_ordered_by_delta_e(client):
    body = (await client.get(f"/hex-search?p={PATTERN}")).json()

    des = [row["delta_e"] for row in body["items"]]
    assert des == sorted(des)


async def test_owners_are_annotated_onto_the_colours_they_hold(client, holders):
    body = (await client.get(f"/hex-search?p={PATTERN}")).json()
    row = next(r for r in body["items"] if r["hex_code"] == "FFFF00")

    assert row["owner_count"] == 1
    owner = row["owners"][0]
    assert owner["username"] == "exact"
    # Serialised as a string by PublicUser, though the column is an integer.
    assert owner["discord_id"] == "21"
    assert sorted(owner["pieces"]) == [Piece.HELMET, Piece.BOOTS] and owner["quantity"] == 2


async def test_unowned_colours_still_appear(client, holders):
    body = (await client.get(f"/hex-search?p={PATTERN}")).json()

    unowned = [r for r in body["items"] if r["owner_count"] == 0]
    assert len(unowned) == 14


async def test_owned_only_collapses_to_held_colours(client, holders):
    body = (await client.get(f"/hex-search?p={PATTERN}&owned_only=true")).json()

    assert body["total"] == 2
    assert {r["hex_code"] for r in body["items"]} == {"FFFF00", "FFFF05"}
    # The pattern still denotes sixteen; only the filtered count moved.
    assert body["pattern_total"] == 16


async def test_the_piece_filter_only_narrows_the_owners(client, holders):
    body = (await client.get(f"/hex-search?p={PATTERN}&pieces=3")).json()
    row = next(r for r in body["items"] if r["hex_code"] == "FFFF00")

    assert body["total"] == 16
    assert [o["pieces"] for o in row["owners"]] == [[Piece.BOOTS]]


async def test_the_piece_filter_with_owned_only_drops_the_colour(client, holders):
    body = (await client.get(f"/hex-search?p={PATTERN}&owned_only=true&pieces=3")).json()

    assert {r["hex_code"] for r in body["items"]} == {"FFFF00"}


@pytest.mark.parametrize("tier", ["t1", "t2", "t3"])
async def test_a_tier_selects_exactly_the_rows_that_earn_it(client, tier):
    """Derived from the unfiltered page rather than pinned: FFFF00 is an exact
    catalog colour, so every FFFF0? neighbour is t1 and a hardcoded 'narrows to
    fewer' would assert nothing."""
    unfiltered = (await client.get(f"/hex-search?p={PATTERN}")).json()["items"]
    expected = {row["hex_code"] for row in unfiltered if row["tier"] == tier}

    body = (await client.get(f"/hex-search?p={PATTERN}&tier={tier}")).json()

    assert body["total"] == len(expected)
    assert {row["hex_code"] for row in body["items"]} == expected


async def test_all_is_not_a_tier_filter(client):
    plain = (await client.get(f"/hex-search?p={PATTERN}")).json()
    every = (await client.get(f"/hex-search?p={PATTERN}&tier=all")).json()

    assert every["total"] == plain["total"] == 16


async def test_paging_is_stable_across_pages(client):
    first = (await client.get(f"/hex-search?p={PATTERN}&limit=5&offset=0")).json()
    second = (await client.get(f"/hex-search?p={PATTERN}&limit=5&offset=5")).json()
    both = (await client.get(f"/hex-search?p={PATTERN}&limit=10")).json()

    assert [r["hex_code"] for r in first["items"] + second["items"]] == [
        r["hex_code"] for r in both["items"]
    ]


async def test_an_unconstrained_pattern_reports_the_whole_space(client):
    body = (await client.get("/hex-search?p=??????&limit=3")).json()

    assert body["pattern_total"] == 0x1000000
    assert len(body["items"]) == 3


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
