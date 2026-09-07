"""GET /seymours/sub-filters -- the /items dropdown's options.

The dropdown used to list all 109 catalog sets for everyone. This narrows it to
what the user holds, which is only safe because available-filters was left
alone: the marketplace reads that one to place a buy order for a set you do not
own yet. The last test here is that regression guard.
"""
import pytest

from app.models import Seymour, User
from app.pieces import Piece

pytestmark = pytest.mark.integration

TARGET = "FFFF00"


@pytest.fixture
async def holder(db, user):
    """Two helmets of one catalog colour, plus an unrelated one."""
    db.add_all([
        Seymour(user_id=user.id, item_name=Piece.HELMET, hex_color=TARGET),
        Seymour(user_id=user.id, item_name=Piece.HELMET, hex_color=TARGET),
        Seymour(user_id=user.id, item_name=Piece.BOOTS, hex_color="FFFE00"),
    ])
    await db.commit()
    return user


async def options(client, filter_type: str) -> list[dict]:
    return (await client.get(f"/seymours/sub-filters?filter={filter_type}")).json()


async def test_it_returns_names_with_counts(client, as_user, holder):
    as_user(holder)
    body = await options(client, "all")

    assert body
    assert all(set(o) == {"name", "count"} for o in body)


async def test_counts_are_items_not_distinct_colours(client, as_user, holder):
    """Two copies of one colour show as two, because selecting the option shows
    two cards."""
    as_user(holder)
    by_name = {o["name"]: o["count"] for o in await options(client, "all")}

    assert sum(by_name.values()) == 3


async def test_sets_are_limited_to_ones_the_user_holds(client, as_user, holder):
    """The whole point: 109 catalog sets, and this user completes none of them."""
    as_user(holder)
    body = await options(client, "sets")

    assert len(body) < 109


async def test_every_name_is_still_a_usable_sub_filter(client, as_user, holder):
    """These strings are the filter key, not a label -- if they drift, the
    dropdown silently selects nothing."""
    as_user(holder)

    for option in await options(client, "all"):
        filtered = (
            await client.get(f"/seymours/?filter=all&sub_filter={option['name']}")
        ).json()
        assert len(filtered) == option["count"], option["name"]


async def test_a_user_with_nothing_gets_no_options(client, as_user, user):
    as_user(user)

    assert await options(client, "all") == []
    assert await options(client, "sets") == []


async def test_it_requires_a_signed_in_user(client):
    assert (await client.get("/seymours/sub-filters?filter=all")).status_code in (401, 403)


async def test_available_filters_still_lists_the_whole_catalog(client, as_user, holder):
    """The marketplace guard. This endpoint must NOT be narrowed to holdings:
    you place buy orders for sets you do not own."""
    as_user(holder)
    catalog = (await client.get("/seymours/available-filters?filter=sets")).json()

    assert len(catalog) > 100
    assert all(isinstance(name, str) for name in catalog)
