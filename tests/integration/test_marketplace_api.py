"""Marketplace endpoints against real rows.

These cover the four bugs the expiry rewrite fixed: reads that wrote to the
database, totals that disagreed with the page, a check-then-insert race that
double-listed a Seymour, and BuyOrder.user being serialised without being
eager-loaded (which made get_order raise MissingGreenlet on every call).
"""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import (
    BuyOrder, BuyOrderSetCriteria, OrderStatus, OrderType, SellOrder, Seymour, Tier,
)

pytestmark = pytest.mark.integration

HEXES = ["FF0000", "00FF00", "0000FF", "AABBCC"]


@pytest.fixture
async def seeded(db, user):
    """Two live buy orders, one lapsed-but-ACTIVE, one cancelled; plus sell orders."""
    now = utcnow()
    seymours = [Seymour(user_id=user.id, item_name=i % 4, hex_color=h)
                for i, h in enumerate(HEXES)]
    db.add_all(seymours)
    await db.flush()

    for status, expires, price in [
        (OrderStatus.ACTIVE, now + timedelta(hours=5), 300),
        (OrderStatus.ACTIVE, now + timedelta(hours=9), 100),
        (OrderStatus.ACTIVE, now - timedelta(hours=2), 200),
        (OrderStatus.CANCELLED, now + timedelta(hours=5), 400),
    ]:
        order = BuyOrder(user_id=user.id, price=price, order_type=OrderType.SET_PIECE,
                         status=status, expires_at=expires)
        db.add(order)
        await db.flush()
        db.add(BuyOrderSetCriteria(order_id=order.id, set_name="Lava",
                                   piece_types=[0, 1], tier=Tier.T1))

    db.add_all([
        SellOrder(user_id=user.id, seymour_id=seymours[0].id, price=50,
                  status=OrderStatus.ACTIVE, expires_at=now + timedelta(hours=5)),
        SellOrder(user_id=user.id, seymour_id=seymours[1].id, price=70,
                  status=OrderStatus.ACTIVE, expires_at=now + timedelta(hours=6)),
        SellOrder(user_id=user.id, seymour_id=seymours[2].id, price=90,
                  status=OrderStatus.ACTIVE, expires_at=now - timedelta(hours=3)),
    ])
    await db.commit()
    return [s.id for s in seymours]


async def test_public_lists_exclude_lapsed_orders(client, seeded):
    body = (await client.get("/marketplace/orders", params={"limit": 50})).json()
    assert body["total"] == 2
    assert len(body["orders"]) == 2
    assert {o["status"] for o in body["orders"]} == {"active"}

    body = (await client.get("/marketplace/orders/sell", params={"limit": 50})).json()
    assert body["total"] == 2
    assert len(body["orders"]) == 2


async def test_own_orders_report_derived_status(client, as_user, user, seeded):
    as_user(user)
    body = (await client.get("/marketplace/orders/mine", params={"limit": 50})).json()

    assert body["total"] == 4
    by_price = {o["price"]: o["status"] for o in body["orders"]}
    assert by_price[300] == "active"
    assert by_price[200] == "expired"
    assert by_price[400] == "cancelled"


async def test_reading_does_not_persist_expiry(client, as_user, user, seeded, db):
    as_user(user)
    await client.get("/marketplace/orders/mine", params={"limit": 50})

    lapsed = await db.scalar(select(BuyOrder.status).where(BuyOrder.price == 200))
    assert lapsed == OrderStatus.ACTIVE


@pytest.mark.parametrize("path, status, expected", [
    ("/marketplace/orders/mine", "active", 2),
    ("/marketplace/orders/mine", "expired", 1),
    ("/marketplace/orders/sell/mine", "active", 2),
    ("/marketplace/orders/sell/mine", "expired", 1),
])
async def test_status_filter_is_expiry_aware(client, as_user, user, seeded, path, status, expected):
    as_user(user)
    body = (await client.get(path, params={"status": status, "limit": 50})).json()
    assert body["total"] == expected


@pytest.mark.parametrize("sort_by", ["price", "expires_at", "delta_e"])
async def test_sell_pagination_pages_are_disjoint(client, seeded, sort_by):
    params = {"limit": 1, "sort_by": sort_by}
    first = (await client.get("/marketplace/orders/sell", params={**params, "skip": 0})).json()
    second = (await client.get("/marketplace/orders/sell", params={**params, "skip": 1})).json()

    assert first["total"] == second["total"] == 2
    assert first["orders"][0]["id"] != second["orders"][0]["id"]


async def test_sell_total_counts_the_whole_match_not_the_page(client, seeded):
    body = (await client.get("/marketplace/orders/sell", params={"limit": 1})).json()
    assert body["total"] == 2
    assert len(body["orders"]) == 1


async def test_endpoints_that_serialise_user_eager_load_it(client, as_user, user, seeded):
    """Each of these 500'd with MissingGreenlet before .user was eager-loaded."""
    as_user(user)
    listed = (await client.get("/marketplace/orders", params={"limit": 1})).json()
    order_id = listed["orders"][0]["id"]

    assert (await client.get(f"/marketplace/orders/{order_id}")).status_code == 200
    assert (await client.get(f"/marketplace/orders/matching/{seeded[0]}",
                             params={"limit": 5})).status_code == 200
    assert (await client.get("/marketplace/orders/fillable", params={"limit": 5})).status_code == 200


async def test_get_order_reports_expired_without_writing(client, as_user, user, seeded, db):
    as_user(user)
    lapsed_id = await db.scalar(select(BuyOrder.id).where(BuyOrder.price == 200))

    body = (await client.get(f"/marketplace/orders/{lapsed_id}")).json()
    assert body["status"] == "expired"

    assert await db.scalar(select(BuyOrder.status).where(BuyOrder.id == lapsed_id)) \
        == OrderStatus.ACTIVE


async def test_concurrent_listing_of_one_seymour_creates_exactly_one(
    client, as_user, user, seeded, db
):
    as_user(user)
    payload = {"seymour_ids": [seeded[3]], "price": 123, "expires_hours": 24}

    responses = await asyncio.gather(
        client.post("/marketplace/orders/sell", json=payload),
        client.post("/marketplace/orders/sell", json=payload),
    )

    count = await db.scalar(
        select(func.count()).select_from(SellOrder).where(SellOrder.seymour_id == seeded[3])
    )
    assert count == 1
    assert sorted(r.status_code for r in responses) == [200, 400]


async def test_created_sell_order_has_the_same_shape_as_a_fetched_one(
    client, as_user, user, seeded
):
    """create() used to return raw ORM rows, so the POST silently omitted
    seymour.match while every GET of the same resource included it."""
    as_user(user)

    created = (await client.post(
        "/marketplace/orders/sell",
        json={"seymour_ids": [seeded[3]], "price": 123, "expires_hours": 24},
    )).json()

    listed = (await client.get(
        "/marketplace/orders/sell/mine", params={"status": "active"}
    )).json()
    fetched = next(o for o in listed["orders"] if o["id"] == created[0]["id"])

    assert created[0] == fetched


async def test_cannot_list_a_seymour_you_do_not_own(client, as_user, other_user, seeded):
    as_user(other_user)
    response = await client.post(
        "/marketplace/orders/sell",
        json={"seymour_ids": [seeded[0]], "price": 10, "expires_hours": 24},
    )
    assert response.status_code == 400


async def test_cancelling_twice_is_rejected(client, as_user, user, seeded, db):
    as_user(user)
    order_id = await db.scalar(select(BuyOrder.id).where(BuyOrder.price == 300))

    assert (await client.delete(f"/marketplace/orders/{order_id}")).status_code == 200
    assert (await client.delete(f"/marketplace/orders/{order_id}")).status_code == 400
