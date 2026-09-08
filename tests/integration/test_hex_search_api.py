"""GET /hex-search and its two auction routes.

Search results are colours somebody in the database holds, so every case there
seeds holdings first. FFFF00 is an exact Pure Yellow catalog colour, which makes
the top row and its tier deterministic. The auction routes seed ended sales
instead -- they filter on the auction's colour, and nobody has to own one.
"""
import pytest

from app.models import EndedAuction, Seymour, User
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


SALE_BASE_MS = 1_700_000_000_000

# hex_color, price, piece, tier, hours old. FFFE00 fails slot 3 and FF0F00
# fails slot 4, so neither is in PATTERN -- they are here to be excluded.
SALES = [
    ("s1", "FFFF00", 1_000, Piece.HELMET, "t1", 3),
    ("s2", "FFFF05", 3_000, Piece.HELMET, "t2", 2),
    ("s3", "FFFF0F", 5_000, Piece.BOOTS, "t1", 1),
    ("s4", "FFFE00", 9_000, Piece.HELMET, "t1", 4),
    ("s5", "FF0F00", 7_000, Piece.HELMET, "t1", 5),
]


@pytest.fixture
async def sales(db):
    """Ended auctions, three of them matching PATTERN. Holdings are irrelevant
    here: the stats filter on the auction's own colour, not on who owns one."""
    db.add_all([
        EndedAuction(
            auction_id=aid,
            seller_uuid="s", buyer_uuid="b",
            price=price, is_bin=False, item_uuid=aid,
            hex_color=hex_color, piece=piece,
            timestamp=SALE_BASE_MS + age * 3_600_000,
            target_hex_keys=hex_color, delta_e_keys=0.0, tier_keys=tier,
            target_hex_all=hex_color, delta_e_all=0.0, tier_all=tier,
        )
        for aid, hex_color, price, piece, tier, age in SALES
    ])
    await db.commit()
    return SALES


class TestPatternAuctionStats:
    async def test_only_colours_the_pattern_matches_are_counted(self, client, sales):
        body = (await client.get(f"/hex-search/auction-stats?p={PATTERN}")).json()

        assert body["pattern"] == PATTERN
        assert body["pattern_total"] == 16
        # 1_000 + 3_000 + 5_000, with FFFE00 and FF0F00 left out.
        assert (body["total"], body["total_volume"]) == (3, 9_000)
        assert body["median_price"] == 3_000

    async def test_a_repeated_variable_is_enforced_in_sql(self, client, sales):
        """The only case the LIKE mask cannot express on its own: FFFF0F has the
        same digit in both variable slots, FFFF00 and FFFF05 do not."""
        body = (await client.get("/hex-search/auction-stats?p=FFFFZZ")).json()

        assert (body["total"], body["total_volume"]) == (1, 5_000)

    async def test_a_pattern_nothing_has_sold_in_is_empty_rather_than_an_error(
        self, client, sales
    ):
        body = (await client.get("/hex-search/auction-stats?p=21????")).json()

        assert body["total"] == 0
        assert body["price_distribution"] == [] and body["median_price"] == 0.0

    async def test_the_tier_filter_narrows_it(self, client, sales):
        body = (await client.get(f"/hex-search/auction-stats?p={PATTERN}&tier=t2")).json()

        assert body["tier"] == "t2"
        assert (body["total"], body["total_volume"]) == (1, 3_000)

    async def test_all_is_the_every_band_sentinel_rather_than_a_band(self, client, sales):
        body = (await client.get(f"/hex-search/auction-stats?p={PATTERN}&tier=all")).json()

        assert body["tier"] is None and body["total"] == 3

    async def test_the_piece_filter_narrows_it(self, client, sales):
        body = (await client.get(f"/hex-search/auction-stats?p={PATTERN}&pieces=3")).json()

        assert (body["total"], body["total_volume"]) == (1, 5_000)

    async def test_a_leading_hash_and_lower_case_reach_the_same_rows(self, client, sales):
        body = (await client.get("/hex-search/auction-stats", params={"p": "#ffff0?"})).json()

        assert body["pattern"] == PATTERN and body["total"] == 3


class TestPatternSales:
    async def test_it_lists_the_matching_sales_newest_first(self, client, sales):
        body = (await client.get(f"/hex-search/sales?p={PATTERN}")).json()

        assert body["total"] == 3
        assert [row["auction_id"] for row in body["items"]] == ["s3", "s2", "s1"]

    async def test_rows_carry_their_own_catalog_match(self, client, sales):
        """Not a distance to the searched colour -- a pattern names a set, so
        there is no single colour to measure from."""
        row = (await client.get(f"/hex-search/sales?p={PATTERN}")).json()["items"][0]

        assert row["hex_color"] == "FFFF0F"
        assert (row["delta_e"], row["tier"]) == (0.0, "t1")
        assert "search_delta_e" not in row

    async def test_it_pages(self, client, sales):
        first = (await client.get(f"/hex-search/sales?p={PATTERN}&limit=2&offset=0")).json()
        second = (await client.get(f"/hex-search/sales?p={PATTERN}&limit=2&offset=2")).json()

        assert [r["auction_id"] for r in first["items"]] == ["s3", "s2"]
        assert [r["auction_id"] for r in second["items"]] == ["s1"]
        assert first["total"] == second["total"] == 3

    async def test_it_agrees_with_the_stats_it_is_listed_under(self, client, sales):
        """Same filters, so the two must describe the same rows."""
        stats = (await client.get(f"/hex-search/auction-stats?p={PATTERN}&tier=t1")).json()
        listed = (await client.get(f"/hex-search/sales?p={PATTERN}&tier=t1")).json()

        assert stats["total"] == listed["total"]
        assert sum(r["price"] for r in listed["items"]) == stats["total_volume"]


@pytest.mark.parametrize("path", ["auction-stats", "sales"])
@pytest.mark.parametrize("raw", ["ZZZZZ", "1234567", "12345!", ""])
async def test_the_auction_routes_refuse_malformed_patterns(client, path, raw):
    response = await client.get(f"/hex-search/{path}", params={"p": raw})

    assert response.status_code in (400, 422)
