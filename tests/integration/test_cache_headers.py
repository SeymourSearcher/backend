"""Cache-Control goes on responses that are the same for everyone, and nowhere else."""
import pytest

from app.dependencies import CATALOG_MAX_AGE, PUBLIC_MAX_AGE

pytestmark = pytest.mark.integration

PUBLIC = [
    "/stats/",
    "/stats/rankings",
    "/stats/custom-sets",
    "/auctions/stats",
    "/auctions/stats?pieces=2",
    "/auctions/top-sales",
    "/hex/FF0000",
    "/hex/FF0000/owners",
    "/hex/FF0000/sales",
    "/hex/FF0000/auction-stats",
    "/hex-search/sales?p=FF??00",
    "/hex-search/auction-stats?p=FF??00",
]

PRIVATE = [
    "/stats/me",
    "/users/me",
    "/hex-search?p=FF??00",
    "/auctions/active",
    "/marketplace/orders/mine",
]


@pytest.mark.parametrize("path", PUBLIC)
async def test_public_data_is_cacheable(client, path):
    response = await client.get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == f"public, max-age={PUBLIC_MAX_AGE}"


@pytest.mark.parametrize("path", ["/catalog/targets", "/auctions/sets"])
async def test_the_catalog_is_cacheable_for_a_day(client, path):
    response = await client.get(path)

    assert response.headers["cache-control"] == f"public, max-age={CATALOG_MAX_AGE}"


@pytest.mark.parametrize("path", PRIVATE)
async def test_user_specific_responses_are_not_marked_public(client, user, as_user, path):
    as_user(user)

    response = await client.get(path)

    assert response.status_code == 200
    assert "public" not in response.headers.get("cache-control", "")


async def test_an_error_is_not_cached(client):
    response = await client.get("/hex/NOTHEX")

    assert response.status_code >= 400
    assert "cache-control" not in response.headers
