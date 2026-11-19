"""The Hypixel auction feed fetch: retries, the concurrency cap, all-or-nothing,
and the per-page Seymour filter."""
import asyncio

import httpx
import pytest

from app.pieces import ITEM_NAMES
from app.services import auctions

TOTAL_PAGES = 20


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(auctions, "RETRY_BACKOFF_SECONDS", 0)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def page_body(page: int) -> dict:
    return {
        "totalPages": TOTAL_PAGES,
        "auctions": [
            {"uuid": f"p{page}", "item_name": f"Fine {ITEM_NAMES[page % len(ITEM_NAMES)]}"},
            {"uuid": f"other{page}", "item_name": "Aspect of the End"},
        ],
    }


def ok(req):
    return httpx.Response(200, json=page_body(int(req.url.params["page"])))


async def fetch_all(handler) -> list[dict]:
    async with client_for(handler) as client:
        return await auctions.get_all_auctions(client)


async def test_it_keeps_the_seymour_listings_of_every_page():
    result = await fetch_all(ok)

    assert sorted(a["uuid"] for a in result) == sorted(f"p{p}" for p in range(TOTAL_PAGES))


async def test_the_client_survives_the_fetch_for_the_next_poll():
    async with client_for(ok) as client:
        await auctions.get_all_auctions(client)
        assert not client.is_closed
        assert len(await auctions.get_all_auctions(client)) == TOTAL_PAGES


async def test_a_transient_failure_is_retried():
    calls: dict[int, int] = {}

    def handler(req):
        page = int(req.url.params["page"])
        calls[page] = calls.get(page, 0) + 1
        if page == 7 and calls[page] == 1:
            return httpx.Response(503)
        return ok(req)

    result = await fetch_all(handler)

    assert len(result) == TOTAL_PAGES
    assert calls[7] == 2


async def test_a_page_that_keeps_failing_fails_the_whole_fetch():
    """Never a partial list: the caller deletes whatever this poll did not see."""
    def handler(req):
        if req.url.params["page"] == "3":
            raise httpx.ReadTimeout("slow page", request=req)
        return ok(req)

    with pytest.raises(httpx.ReadTimeout):
        await fetch_all(handler)


async def test_a_client_error_is_not_retried():
    calls = []

    def handler(req):
        calls.append(req.url.params["page"])
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_all(handler)
    assert calls == ["0"]


async def test_pages_are_fetched_at_most_page_concurrency_at_a_time():
    in_flight = peak = 0

    async def handler(req):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return ok(req)

    await fetch_all(handler)

    assert 1 < peak <= auctions.PAGE_CONCURRENCY
