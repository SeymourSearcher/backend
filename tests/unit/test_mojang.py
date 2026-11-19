"""The upstream client's whole point is a shared pool, per-host pacing, and only
ever remembering an affirmative "no such account".

The concurrency test deliberately runs against a real socket server: an httpx
MockTransport bypasses the connection pool entirely, so it reports whatever
concurrency asyncio.gather produces and would pass with any limit configured.
"""
import asyncio
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.clock import utcnow
from app.services import mojang


@pytest.fixture(autouse=True)
async def reset_client_state():
    def clear():
        mojang._failures.clear()
        mojang.profile_limiter.reset()
        mojang.bulk_limiter.reset()

    clear()
    await mojang.close_client()
    yield
    clear()
    await mojang.close_client()


class _ScriptedClient:
    """Replays a canned sequence of responses, repeating the last one."""

    def __init__(self, *responses, payload=None):
        self.responses = responses
        self.payload = {mojang.PROFILE_NAME_KEY: "someone"} if payload is None else payload
        self.calls = 0
        self.bodies = []

    def _next(self, url):
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        status, headers = item
        body = self.payload(self.calls) if callable(self.payload) else self.payload
        return httpx.Response(status, headers=headers or {}, json=body,
                              request=httpx.Request("GET", url))

    async def get(self, url):
        return self._next(url)

    async def post(self, url, json):
        self.bodies.append(json)
        return self._next(url)


@pytest.fixture
def scripted(monkeypatch):
    def install(*responses, payload=None):
        client = _ScriptedClient(*responses, payload=payload)

        async def get_client():
            return client

        monkeypatch.setattr(mojang, "get_client", get_client)
        return client

    return install


async def test_get_client_is_shared_and_capped():
    first, second = await mojang.get_client(), await mojang.get_client()
    assert first is second
    assert first._transport._pool._max_connections == mojang.MAX_CONNECTIONS


async def test_close_client_allows_a_fresh_one():
    first = await mojang.get_client()
    await mojang.close_client()
    assert await mojang.get_client() is not first


async def test_get_client_is_race_safe():
    clients = await asyncio.gather(*[mojang.get_client() for _ in range(20)])
    assert len({id(c) for c in clients}) == 1


def test_record_and_expire_failures():
    mojang._record_failure("abc")
    assert mojang._recently_failed("abc") is True
    assert mojang._recently_failed("other") is False

    mojang._failures["abc"] = utcnow() - mojang.FAILURE_TTL - timedelta(seconds=1)
    assert mojang._recently_failed("abc") is False


def test_failure_map_evicts_stale_entries_at_the_cap():
    stale = utcnow() - mojang.FAILURE_TTL - timedelta(minutes=1)
    mojang._failures.update({f"old{i}": stale for i in range(mojang.MAX_FAILURES_TRACKED)})

    mojang._record_failure("fresh")

    assert len(mojang._failures) == 1
    assert "fresh" in mojang._failures


async def test_recently_failed_uuid_skips_the_upstream_call(scripted):
    client = scripted((200, None))
    mojang._record_failure("dead")

    assert await mojang.name_for_uuid("dead") is None
    assert client.calls == 0


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
            body = json.dumps({mojang.PROFILE_NAME_KEY: "someone"}).encode()
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
    monkeypatch.setattr(mojang, "PROFILE_URL", local_api)
    monkeypatch.setattr(mojang, "MAX_CONNECTIONS", 5)
    # The pool is what's under test here; leave pacing out of it, or the rate
    # limiter becomes the binding constraint and peak concurrency drops to ~1.
    monkeypatch.setattr(mojang.profile_limiter, "rps", 10_000)

    results = await asyncio.gather(*[mojang.name_for_uuid(f"uuid{i}") for i in range(40)])

    assert results == ["someone"] * 40
    assert _Handler.peak <= 5, f"pool cap not enforced: {_Handler.peak} concurrent"
    assert _Handler.peak > 1, "requests serialised; the test is not measuring anything"


async def test_a_404_is_the_only_remembered_failure(scripted):
    scripted((404, None))

    assert await mojang.name_for_uuid("gone") is None
    assert mojang._recently_failed("gone") is True


async def test_rate_limiting_is_retried_and_never_remembered(scripted):
    client = scripted((429, {"retry-after": "0"}), (200, None))

    assert await mojang.name_for_uuid("busy") == "someone"
    assert client.calls == 2
    assert mojang._recently_failed("busy") is False


async def test_persistent_rate_limiting_gives_up_without_remembering(scripted):
    client = scripted((429, {"retry-after": "0"}))

    assert await mojang.name_for_uuid("busy") is None
    assert client.calls == mojang.MAX_ATTEMPTS
    # Crucially not recorded: a re-run must retry it.
    assert mojang._recently_failed("busy") is False


async def test_connection_errors_are_retried_and_not_remembered(scripted):
    client = scripted(httpx.RequestError("boom"))

    assert await mojang.name_for_uuid("uuid") is None
    assert client.calls == mojang.MAX_ATTEMPTS
    assert mojang._recently_failed("uuid") is False


async def test_a_429_stalls_every_caller_not_just_its_own(scripted, monkeypatch):
    # One attempt only: retrying would correctly sit out the cooldown it just
    # set, which is the point of the feature but makes for a 60s test.
    monkeypatch.setattr(mojang, "MAX_ATTEMPTS", 1)
    scripted((429, {"retry-after": "30"}))
    await mojang.name_for_uuid("busy")

    remaining = mojang.profile_limiter._cooldown_until - asyncio.get_running_loop().time()
    assert 25 < remaining <= mojang.MAX_COOLDOWN


async def test_cooldown_is_capped(scripted, monkeypatch):
    monkeypatch.setattr(mojang, "MAX_ATTEMPTS", 1)
    scripted((429, {"retry-after": "99999"}))
    await mojang.name_for_uuid("busy")

    remaining = mojang.profile_limiter._cooldown_until - asyncio.get_running_loop().time()
    assert remaining <= mojang.MAX_COOLDOWN


async def test_the_two_hosts_have_independent_cooldowns(scripted, monkeypatch):
    monkeypatch.setattr(mojang, "MAX_ATTEMPTS", 1)
    scripted((429, {"retry-after": "30"}))
    await mojang.name_for_uuid("busy")

    assert mojang.profile_limiter._cooldown_until > 0
    assert mojang.bulk_limiter._cooldown_until == 0


async def test_bulk_lookup_chunks_at_the_server_enforced_cap(scripted):
    client = scripted((200, None), payload=[])
    names = [f"name{i}" for i in range(25)]

    await mojang.uuids_for_names(names)

    assert client.calls == 3
    assert [len(b) for b in client.bodies] == [10, 10, 5]
    assert client.bodies[0] == names[:10]


async def test_bulk_lookup_keys_on_lowercased_names(scripted):
    scripted((200, None), payload=[{"name": "MiXeDcAsE", "id": "abc123"}])

    assert await mojang.uuids_for_names(["mixedcase"]) == {"mixedcase": "abc123"}


async def test_bulk_lookup_omits_names_upstream_does_not_know(scripted):
    scripted((200, None), payload=[{"name": "here", "id": "abc"}])

    found = await mojang.uuids_for_names(["here", "gone"])

    assert found == {"here": "abc"}
    assert "gone" not in found


async def test_one_bad_chunk_does_not_lose_the_others(scripted, monkeypatch):
    monkeypatch.setattr(mojang, "MAX_ATTEMPTS", 1)
    # First chunk 400s, second succeeds.
    client = scripted((400, None), (200, None),
                      payload=lambda n: [] if n == 1 else [{"name": "late", "id": "xyz"}])

    found = await mojang.uuids_for_names([f"n{i}" for i in range(11)])

    assert client.calls == 2
    assert found == {"late": "xyz"}


async def test_bulk_lookup_of_nothing_makes_no_request(scripted):
    client = scripted((200, None), payload=[])

    assert await mojang.uuids_for_names([]) == {}
    assert client.calls == 0
