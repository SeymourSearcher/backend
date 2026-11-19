"""The Hypixel auction feed fetch: retries, the concurrency cap, all-or-nothing."""
import asyncio
from functools import partial

import httpx
import pytest

from app.services import auctions

TOTAL_PAGES = 20


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(auctions, "RETRY_BACKOFF_SECONDS", 0)


def serve(monkeypatch, handler):
    """Route every client get_all_auctions opens through `handler`."""
    real = httpx.AsyncClient
    monkeypatch.setattr(
        auctions.httpx, "AsyncClient",
        partial(real, transport=httpx.MockTransport(handler)),
    )


def page_body(page: int) -> dict:
    return {"totalPages": TOTAL_PAGES, "auctions": [{"uuid": f"p{page}"}]}


async def test_it_concatenates_every_page(monkeypatch):
    serve(monkeypatch, lambda req: httpx.Response(200, json=page_body(int(req.url.params["page"]))))

    result = await auctions.get_all_auctions()

    assert sorted(a["uuid"] for a in result) == sorted(f"p{p}" for p in range(TOTAL_PAGES))


async def test_a_transient_failure_is_retried(monkeypatch):
    calls: dict[int, int] = {}

    def handler(req):
        page = int(req.url.params["page"])
        calls[page] = calls.get(page, 0) + 1
        if page == 7 and calls[page] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=page_body(page))

    serve(monkeypatch, handler)

    result = await auctions.get_all_auctions()

    assert len(result) == TOTAL_PAGES
    assert calls[7] == 2


async def test_a_page_that_keeps_failing_fails_the_whole_fetch(monkeypatch):
    """Never a partial list: the caller sweeps whatever this poll did not see."""
    def handler(req):
        if req.url.params["page"] == "3":
            raise httpx.ReadTimeout("slow page", request=req)
        return httpx.Response(200, json=page_body(int(req.url.params["page"])))

    serve(monkeypatch, handler)

    with pytest.raises(httpx.ReadTimeout):
        await auctions.get_all_auctions()


async def test_a_client_error_is_not_retried(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.params["page"])
        return httpx.Response(404)

    serve(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        await auctions.get_all_auctions()
    assert calls == ["0"]


async def test_pages_are_fetched_at_most_page_concurrency_at_a_time(monkeypatch):
    in_flight = peak = 0

    async def handler(req):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json=page_body(int(req.url.params["page"])))

    serve(monkeypatch, handler)

    await auctions.get_all_auctions()

    assert 1 < peak <= auctions.PAGE_CONCURRENCY
