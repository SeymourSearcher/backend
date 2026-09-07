"""What one unauthenticated -- or one ordinary authenticated -- caller can cost.

Three separate ceilings, none of which existed before: a per-IP request limit on
every route, a throttle on the login callback, and the inventory cap on the
custom-set filter. The last is the sharpest: find_custom_sets grows
superlinearly and used to run inline on the event loop, so a single request
against a large collection froze every other request on the worker.
"""
import httpx
import pytest

from app.dependencies import GLOBAL_LIMIT
from app.models import Seymour
from app.pieces import Piece
from app.routers.auth import CALLBACK_LIMIT
from app.services import auth, custom_sets

pytestmark = pytest.mark.integration


async def test_the_global_limit_eventually_refuses(client, monkeypatch):
    monkeypatch.setattr("app.dependencies.GLOBAL_LIMIT", GLOBAL_LIMIT._replace(limit=3))

    codes = [(await client.get("/marketplace/sets-pieces")).status_code for _ in range(4)]

    assert codes == [200, 200, 200, 429]


async def test_health_is_exempt_from_the_global_limit(client, monkeypatch):
    """The swarm healthcheck runs every 10s from inside the container."""
    monkeypatch.setattr("app.dependencies.GLOBAL_LIMIT", GLOBAL_LIMIT._replace(limit=1))

    for _ in range(3):
        assert (await client.get("/health")).status_code == 200


async def test_the_login_callback_is_throttled(client, monkeypatch):
    monkeypatch.setattr("app.routers.auth.CALLBACK_LIMIT", CALLBACK_LIMIT._replace(limit=2))

    async def refuse(code):
        raise httpx.HTTPStatusError("bad", request=None, response=None)

    monkeypatch.setattr(auth, "exchange_code", refuse)

    codes = [
        (await client.post("/auth/discord/callback", json={"code": "x"})).status_code
        for _ in range(3)
    ]

    # The first two get as far as the stubbed exchange; only the third is refused.
    assert codes[:2] == [400, 400]
    assert codes[2] == 429


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
