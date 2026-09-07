"""What one unauthenticated -- or one ordinary authenticated -- caller can cost.

Four ceilings, none of which existed before: a per-IP limit on every route, a
tighter one on the hex pages, a throttle on the login callback, and the inventory
cap on the custom-set filter. The last is the sharpest: find_custom_sets grows
superlinearly and used to run inline on the event loop, so a single request
against a large collection froze every other request on the worker.

The limits are exercised by pre-filling their buckets rather than by lowering
them, so the numbers in app/routers/* stay the ones under test.
"""
import httpx
import pytest

from app.models import Seymour
from app.pieces import Piece
from app.rate_limit import GLOBAL_LIMIT, KEY_PREFIX, RateLimit
from app.routers.auth import CALLBACK_LIMIT
from app.routers.hex import HEX_LIMIT
from app.services import auth, custom_sets

pytestmark = pytest.mark.integration

CALLER = "203.0.113.9"
OTHER_CALLER = "203.0.113.10"


def sent_from(ip: str) -> dict[str, str]:
    """client_ip reads CF-Connecting-IP first; production sits behind Cloudflare."""
    return {"CF-Connecting-IP": ip}


async def exhaust(redis, rl: RateLimit, ip: str = CALLER) -> None:
    await redis.setex(f"{KEY_PREFIX}{rl.bucket}:{ip}", rl.window, rl.limit)


async def test_the_global_limit_refuses_a_caller_that_has_spent_it(client, redis):
    await exhaust(redis, GLOBAL_LIMIT)

    response = await client.get("/marketplace/sets-pieces", headers=sent_from(CALLER))

    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["detail"]


async def test_the_limits_are_per_caller_not_shared(client, redis):
    """The bug this guards: behind traefik every visitor looked like the proxy,
    so one attacker drained the bucket for everyone."""
    await exhaust(redis, GLOBAL_LIMIT)

    response = await client.get("/marketplace/sets-pieces", headers=sent_from(OTHER_CALLER))

    assert response.status_code == 200


async def test_health_is_exempt_from_the_global_limit(client, redis):
    """The swarm healthcheck runs every 10s from inside the container."""
    await exhaust(redis, GLOBAL_LIMIT)

    assert (await client.get("/health", headers=sent_from(CALLER))).status_code == 200


async def test_the_hex_pages_have_their_own_tighter_limit(client, redis):
    """Their cache is keyed by hex code, so a walk misses on every request."""
    assert HEX_LIMIT.limit < GLOBAL_LIMIT.limit
    await exhaust(redis, HEX_LIMIT)

    refused = await client.get("/hex/FF0000", headers=sent_from(CALLER))
    allowed = await client.get("/hex/FF0000", headers=sent_from(OTHER_CALLER))

    assert (refused.status_code, allowed.status_code) == (429, 200)


async def test_the_login_callback_is_throttled(client, redis, monkeypatch):
    async def refuse(code):
        raise httpx.HTTPStatusError("bad", request=None, response=None)

    monkeypatch.setattr(auth, "exchange_code", refuse)
    body = {"code": "x"}

    # Reaches the stubbed exchange and fails there, so the throttle is what
    # changes once the bucket is spent -- not the outcome of the login.
    first = await client.post("/auth/discord/callback", json=body, headers=sent_from(CALLER))
    await exhaust(redis, CALLBACK_LIMIT)
    second = await client.post("/auth/discord/callback", json=body, headers=sent_from(CALLER))

    assert (first.status_code, second.status_code) == (400, 429)


@pytest.fixture
async def collection(db, user):
    for piece, hex_color in enumerate(["FF0000", "FF0001", "FF0100", "FF0101"]):
        db.add(Seymour(user_id=user.id, item_name=Piece(piece), hex_color=hex_color))
    await db.commit()


async def test_the_custom_filter_refuses_an_oversized_collection(
    client, as_user, user, collection, monkeypatch
):
    monkeypatch.setattr("app.services.custom_sets.MAX_INVENTORY", 2)

    def explode(*_args, **_kwargs):
        raise AssertionError("searched a collection over the ceiling")

    monkeypatch.setattr("app.services.seymours.find_custom_sets", explode)
    as_user(user)

    response = await client.get("/seymours/", params={"filter": "custom"})

    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


async def test_the_custom_filter_still_serves_a_collection_under_the_ceiling(
    client, as_user, user, collection
):
    assert custom_sets.MAX_INVENTORY > 4
    as_user(user)

    assert (await client.get("/seymours/", params={"filter": "custom"})).status_code == 200
