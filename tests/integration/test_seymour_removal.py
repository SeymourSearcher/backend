"""DELETE /seymours/ -- clearing an upload.

The endpoint existed but nothing called it, so its side effects were untested.
Two of them are easy to get wrong: sell listings vanish through a MySQL FK
cascade rather than through any code here, and the last_uploaded stamp used to
survive a wipe and go on dating a file that no longer existed.
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import (
    BuyOrder,
    OrderStatus,
    OrderType,
    SellOrder,
    Seymour,
    User,
    UserStats,
)
from app.pieces import Piece

pytestmark = pytest.mark.integration

HEXES = ["FF0000", "00FF00", "0000FF", "AABBCC"]


@pytest.fixture
async def collection(db, user):
    """Four Seymours, two listed for sale, one listing already expired."""
    rows = [
        Seymour(user_id=user.id, item_name=Piece(i % 4), hex_color=hex_color)
        for i, hex_color in enumerate(HEXES)
    ]
    db.add_all(rows)
    user.last_uploaded = utcnow()
    await db.flush()

    now = utcnow()
    db.add_all([
        SellOrder(user_id=user.id, seymour_id=rows[0].id, price=50,
                  status=OrderStatus.ACTIVE, expires_at=now + timedelta(hours=5)),
        SellOrder(user_id=user.id, seymour_id=rows[1].id, price=70,
                  status=OrderStatus.ACTIVE, expires_at=now + timedelta(hours=6)),
        # Past its expiry, so it is not "active" and must not be counted as lost.
        SellOrder(user_id=user.id, seymour_id=rows[2].id, price=90,
                  status=OrderStatus.ACTIVE, expires_at=now - timedelta(hours=3)),
    ])
    await db.commit()
    return rows


async def count(db, model, **where) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return await db.scalar(query)


async def test_it_reports_how_many_seymours_it_removed(client, as_user, user, collection, db):
    as_user(user)
    body = (await client.delete("/seymours/")).json()

    assert body["removed"] == len(HEXES)
    assert await count(db, Seymour, user_id=user.id) == 0


async def test_it_reports_the_listings_the_cascade_takes(client, as_user, user, collection, db):
    """The two live ones. The expired listing is already not for sale, so
    counting it would overstate what the user is about to lose."""
    as_user(user)
    body = (await client.delete("/seymours/")).json()

    assert body["sell_orders_cancelled"] == 2


async def test_the_listings_really_do_go(client, as_user, user, collection, db):
    """Nothing in the service deletes them -- MySQL does, through the FK on
    sell_orders.seymour_id. If that ever stops cascading the count above would
    still read 2 and quietly lie."""
    as_user(user)
    await client.delete("/seymours/")

    assert await count(db, SellOrder, user_id=user.id) == 0


async def test_it_clears_the_upload_stamp(client, as_user, user, collection, db):
    as_user(user)
    await client.delete("/seymours/")

    await db.refresh(user)
    assert user.last_uploaded is None


async def test_the_leaderboard_row_goes_with_the_collection(
    client, as_user, user, collection, db
):
    as_user(user)
    await client.delete("/seymours/")

    assert await count(db, UserStats, user_id=user.id) == 0


async def test_the_account_and_its_buy_orders_survive(client, as_user, user, collection, db):
    """Buy orders are wants, not holdings -- they reference no Seymour and have
    no reason to go when a collection does."""
    db.add(BuyOrder(user_id=user.id, price=100, order_type=OrderType.SET_PIECE,
                    status=OrderStatus.ACTIVE))
    await db.commit()

    as_user(user)
    await client.delete("/seymours/")

    assert await count(db, User, id=user.id) == 1
    assert await count(db, BuyOrder, user_id=user.id) == 1


async def test_it_only_touches_the_caller(client, as_user, user, other_user, collection, db):
    db.add(Seymour(user_id=other_user.id, item_name=Piece.HELMET, hex_color="123456"))
    await db.commit()

    as_user(user)
    await client.delete("/seymours/")

    assert await count(db, Seymour, user_id=other_user.id) == 1


async def test_removing_an_empty_collection_is_a_no_op_not_an_error(
    client, as_user, user, db
):
    as_user(user)
    body = (await client.delete("/seymours/")).json()

    assert body == {"removed": 0, "sell_orders_cancelled": 0}


async def test_it_requires_a_signed_in_user(client):
    assert (await client.delete("/seymours/")).status_code in (401, 403)
