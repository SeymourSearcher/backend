"""GET /stats/me/valuation, and the folding behind it.

The arithmetic is easy to get subtly wrong in ways that still look plausible on
screen, so these pin the three rules that are not obvious from the numbers:
duplicates count their excess only, unpriceable items are excluded rather than
zeroed, and the categories overlap on purpose.
"""
import pytest

from app.models import EndedAuction, Seymour, User
from app.pieces import Piece
from app.services.collection_value import value_collection

pytestmark = pytest.mark.integration

BASE_MS = 1_700_000_000_000

# An exact catalog colour, so the match is deterministic and lands in t1.
TARGET = "FFFF00"
PRICE = 1_000


@pytest.fixture
async def sales(db):
    """Three sales of TARGET at t1, so its median is exactly PRICE."""
    db.add_all([
        EndedAuction(
            auction_id=f"v{i}", seller_uuid="s", buyer_uuid="b",
            price=PRICE, is_bin=False, item_uuid=f"v{i}",
            hex_color=TARGET, piece=Piece.HELMET, timestamp=BASE_MS,
            target_hex_keys=TARGET, delta_e_keys=0.0, tier_keys="t1",
            target_hex_all=TARGET, delta_e_all=0.0, tier_all="t1",
        )
        for i in range(3)
    ])
    await db.commit()


async def value_of(db, redis, seymour_list):
    return await value_collection(db, redis, seymour_list, include_dyes=False)


async def test_an_empty_collection_is_zeroes_not_an_error(db, redis):
    value = await value_of(db, redis, [])

    assert value["total"] == 0
    assert value["priced"] == 0
    assert value["unpriced"] == 0
    assert set(value["categories"]) == {
        "stars", "words", "dupes", "t1", "t2", "t3", "sets", "dyes"
    }


async def test_each_item_contributes_its_colours_median(db, redis, sales):
    value = await value_of(db, redis, [(TARGET, Piece.HELMET)])

    assert value["total"] == PRICE
    assert value["priced"] == 1
    assert value["categories"]["t1"] == PRICE


async def test_every_copy_counts_toward_the_total(db, redis, sales):
    """A collection is worth the sum of its items, duplicates included."""
    value = await value_of(db, redis, [(TARGET, Piece.HELMET)] * 3)

    assert value["total"] == 3 * PRICE
    assert value["priced"] == 3


async def test_dupes_value_the_excess_copies_only(db, redis, sales):
    """Matching count_dupes, which counts n-1 per group -- so the chip's count
    and its value describe the same items."""
    value = await value_of(db, redis, [(TARGET, Piece.HELMET)] * 3)

    assert value["categories"]["dupes"] == 2 * PRICE


async def test_a_single_copy_is_not_a_dupe(db, redis, sales):
    value = await value_of(db, redis, [(TARGET, Piece.HELMET)])

    assert value["categories"]["dupes"] == 0


async def test_an_item_with_no_comparable_sales_is_excluded_not_zeroed(db, redis):
    """No auctions exist at all here, so nothing is priceable."""
    value = await value_of(db, redis, [(TARGET, Piece.HELMET)])

    assert value["total"] == 0
    assert value["priced"] == 0
    assert value["unpriced"] == 1


async def test_priced_and_unpriced_account_for_every_item(db, redis, sales):
    """000000 is 137 away from TARGET and matches no catalog colour at all."""
    collection = [(TARGET, Piece.HELMET), ("000000", Piece.BOOTS)]

    value = await value_of(db, redis, collection)

    assert value["priced"] + value["unpriced"] == len(collection)


async def test_the_categories_overlap_rather_than_partition(db, redis, sales):
    """FFFF00 is a star (repeating pattern) and a t1 match, so it appears in
    both. The UI says the chips do not sum to the total; this is why."""
    value = await value_of(db, redis, [(TARGET, Piece.HELMET)])

    assert value["categories"]["stars"] == PRICE
    assert value["categories"]["t1"] == PRICE
    assert value["total"] == PRICE


class TestEndpoint:
    @pytest.fixture
    async def owner(self, db, user, sales):
        db.add(Seymour(user_id=user.id, item_name=Piece.HELMET, hex_color=TARGET))
        await db.commit()
        return user

    async def test_it_reports_the_signed_in_users_collection(self, client, as_user, owner):
        as_user(owner)
        body = (await client.get("/stats/me/valuation")).json()

        assert body["total"] == PRICE
        assert body["priced"] == 1
        assert body["categories"]["t1"] == PRICE

    async def test_it_requires_a_signed_in_user(self, client):
        assert (await client.get("/stats/me/valuation")).status_code in (401, 403)

    async def test_a_user_with_nothing_gets_zeroes(self, client, as_user, user, sales):
        as_user(user)
        body = (await client.get("/stats/me/valuation")).json()

        assert body == {
            "total": 0,
            "priced": 0,
            "unpriced": 0,
            "categories": {
                "stars": 0, "words": 0, "dupes": 0,
                "t1": 0, "t2": 0, "t3": 0, "sets": 0, "dyes": 0,
            },
        }

    async def test_the_counts_endpoint_still_works_alongside_it(self, client, as_user, owner):
        """Valuation is a separate call precisely so /stats/me stays fast; both
        must describe the same one item."""
        as_user(owner)
        stats = (await client.get("/stats/me")).json()
        value = (await client.get("/stats/me/valuation")).json()

        assert stats["total_seymours"] == 1
        assert value["priced"] + value["unpriced"] == 1
