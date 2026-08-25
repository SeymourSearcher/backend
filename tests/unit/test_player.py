"""The username lookup's whole point is bounded concurrency and a shared client.

The concurrency test deliberately runs against a real socket server: an httpx
MockTransport bypasses the connection pool entirely, so it reports whatever
concurrency asyncio.gather produces and would pass with any limit configured.
"""
import asyncio
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.clock import utcnow
from app.services import player


@pytest.fixture(autouse=True)
async def reset_client_state():
    player._failures.clear()
    await player.close_client()
    yield
    player._failures.clear()
    await player.close_client()


async def test_get_client_is_shared_and_capped():
    first, second = await player.get_client(), await player.get_client()
    assert first is second
    assert first._transport._pool._max_connections == player.MAX_CONNECTIONS


async def test_close_client_allows_a_fresh_one():
    first = await player.get_client()
    await player.close_client()
    assert await player.get_client() is not first


async def test_get_client_is_race_safe():
    clients = await asyncio.gather(*[player.get_client() for _ in range(20)])
    assert len({id(c) for c in clients}) == 1


def test_record_and_expire_failures():
    player._record_failure("abc")
    assert player._recently_failed("abc") is True
    assert player._recently_failed("other") is False

    player._failures["abc"] = utcnow() - player.FAILURE_TTL - timedelta(seconds=1)
    assert player._recently_failed("abc") is False


def test_failure_map_evicts_stale_entries_at_the_cap():
    stale = utcnow() - player.FAILURE_TTL - timedelta(minutes=1)
    player._failures.update({f"old{i}": stale for i in range(player.MAX_FAILURES_TRACKED)})

    player._record_failure("fresh")

    assert len(player._failures) == 1
    assert "fresh" in player._failures


async def test_recently_failed_uuid_skips_the_upstream_call():
    player._record_failure("dead")

    async def explode(*_a, **_kw):
        raise AssertionError("upstream should not be called for a recent failure")

    client = await player.get_client()
    client.get = explode
    assert await player.fetch_username_from_api("dead") is None


class _Handler(BaseHTTPRequestHandler):
    """Holds each request open briefly so overlapping requests are observable."""

    peak = 0
    live = 0
    lock = threading.Lock()

    def do_GET(self):
        with _Handler.lock:
            _Handler.live += 1
            _Handler.peak = max(_Handler.peak, _Handler.live)
        try:
            import time
            time.sleep(0.15)
            body = b'{"username": "someone"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with _Handler.lock:
                _Handler.live -= 1

    def log_message(self, *args):
        pass


@pytest.fixture
def local_api():
    _Handler.peak = _Handler.live = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/{{}}"
    server.shutdown()
    server.server_close()


async def test_pool_bounds_in_flight_requests(local_api, monkeypatch):
    monkeypatch.setattr(player, "API_URL", local_api)
    monkeypatch.setattr(player, "MAX_CONNECTIONS", 5)

    results = await asyncio.gather(
        *[player.fetch_username_from_api(f"uuid{i}") for i in range(40)]
    )

    assert results == ["someone"] * 40
    assert _Handler.peak <= 5, f"pool cap not enforced: {_Handler.peak} concurrent"
    assert _Handler.peak > 1, "requests serialised; the test is not measuring anything"


async def test_connection_error_is_recorded_not_raised(monkeypatch):
    monkeypatch.setattr(player, "API_URL", "http://127.0.0.1:1/{}")

    assert await player.fetch_username_from_api("uuid") is None
    assert player._recently_failed("uuid") is True
