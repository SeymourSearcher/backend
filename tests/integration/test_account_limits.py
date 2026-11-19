"""Ceilings keyed on the signed-in account rather than the caller's address.

Rotating CF-Connecting-IP resets every per-IP limit, so the writes that cost
everyone something -- collection rebuilds and the order book -- are also bounded
per user. Buckets are pre-filled rather than lowered, as in test_abuse_guards.
"""
import pytest

from app.models import BuyOrder, OrderType
from app.rate_limit import KEY_PREFIX, RateLimit
from app.routers.marketplace import ORDER_CREATE_LIMIT
from app.routers.seymours import COLLECTION_WRITE_LIMIT
from app.services.buy_orders import MAX_ACTIVE_BUY_ORDERS

pytestmark = pytest.mark.integration

HEX_ORDER = {
    "price": 1000,
    "order_type": OrderType.HEX_PATTERN.value,
    "hex_criteria": {"match_type": "exact", "hex_value": "FF0000"},
}


async def exhaust(redis, rl: RateLimit, user) -> None:
    await redis.setex(f"{KEY_PREFIX}{rl.bucket}:user:{user.id}", rl.window, rl.limit)


async def test_collection_writes_are_limited_per_account(
    client, redis, as_user, user, other_user
):
    await exhaust(redis, COLLECTION_WRITE_LIMIT, user)

    as_user(user)
    refused = await client.delete("/seymours/", headers={"CF-Connecting-IP": "198.51.100.7"})
    as_user(other_user)
    allowed = await client.delete("/seymours/")

    assert (refused.status_code, allowed.status_code) == (429, 200)


async def test_order_creation_is_limited_per_account(client, redis, as_user, user):
    await exhaust(redis, ORDER_CREATE_LIMIT, user)
    as_user(user)

    response = await client.post("/marketplace/orders", json=HEX_ORDER)

    assert response.status_code == 429


async def test_a_user_cannot_exceed_the_active_buy_order_cap(client, db, as_user, user):
    db.add_all(
        BuyOrder(user_id=user.id, price=1, order_type=OrderType.HEX_PATTERN)
        for _ in range(MAX_ACTIVE_BUY_ORDERS)
    )
    await db.commit()
    as_user(user)

    response = await client.post("/marketplace/orders", json=HEX_ORDER)

    assert response.status_code == 400
    assert "at most" in response.json()["detail"]


@pytest.mark.parametrize("price", [0, 2**31])
async def test_prices_outside_the_int_column_are_rejected(client, as_user, user, price):
    as_user(user)

    response = await client.post("/marketplace/orders", json={**HEX_ORDER, "price": price})

    assert response.status_code == 422
