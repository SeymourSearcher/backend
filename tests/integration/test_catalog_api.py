import pytest

from app.services.compute import HEX_TO_ITEMS

pytestmark = pytest.mark.integration


async def test_it_lists_each_catalog_colour_once(client):
    body = (await client.get("/catalog/targets")).json()

    hexes = [row["hex"] for row in body]
    assert len(hexes) == len(set(hexes)) == len(HEX_TO_ITEMS)
    assert "FF0000" in hexes and all(row["name"] for row in body)
