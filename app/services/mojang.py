"""Mojang API client: the only module that talks to upstream.

Two endpoints, in opposite directions, on separate hosts:

  name_for_uuid     uuid -> name     one per request
  uuids_for_names   name -> uuid     ten per request

No bulk uuid->name endpoint exists (verified: /profile/lookup/bulk/byuuid is
404, and /profiles/minecraft rejects uuids as invalid profile names). So a uuid
we have never seen costs a whole request, while re-checking a name we already
hold costs a tenth of one. app/services/player.py exploits that asymmetry.

Separate hosts mean separate rate limits, hence a limiter each. Both rates are
measured, not guessed -- see the constants. Short bursts prove nothing here:
every candidate host absorbs 30 requests at 8/s, and only a run of a few hundred
reveals the sustained budget.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from app.clock import utcnow as _utcnow

logger = logging.getLogger(__name__)

# Earlier measurement at 4/s favoured the api.ashcon.app proxy (250/250 vs 215/250
# here), but by 2026-11 ashcon refused nearly every request from production
# (~10k 429s/hour) while this host served 60/60 at 2/s. Re-measure before
# switching back.
PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile/lookup/{}"
PROFILE_NAME_KEY = "name"

BULK_URL = "https://api.mojang.com/profiles/minecraft"

# Server-enforced: an 11th name fails the whole request with CONSTRAINT_VIOLATION.
BULK_SIZE = 10

# Both measured over a few hundred sustained requests, not a burst.
PROFILE_RPS = 2
BULK_RPS = 10

MAX_CONNECTIONS = 25
REQUEST_TIMEOUT = 5.0
MAX_ATTEMPTS = 3
DEFAULT_RETRY_AFTER = 5.0
MAX_COOLDOWN = 60.0

# Only a 404 lands here -- a uuid upstream affirmatively does not know. Transient
# refusals must never be cached as failures, or a rate-limit blip turns into 15
# minutes of pretending an account does not exist.
FAILURE_TTL = timedelta(minutes=15)
MAX_FAILURES_TRACKED = 10_000

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_failures: dict[str, datetime] = {}


class Limiter:
    """Paces one host, and stalls every caller of it during a cooldown.

    Slot bookkeeping happens under the lock; the wait itself does not, so
    callers queue rather than serialise.
    """

    def __init__(self, rps: float):
        self.rps = rps
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self._cooldown_until = 0.0

    def reset(self) -> None:
        self._next_slot = self._cooldown_until = 0.0

    async def slot(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            start = max(loop.time(), self._next_slot, self._cooldown_until)
            self._next_slot = start + 1 / self.rps
            delay = start - loop.time()

        if delay > 0:
            await asyncio.sleep(delay)

    def begin_cooldown(self, response: httpx.Response) -> None:
        try:
            seconds = float(response.headers.get("retry-after", DEFAULT_RETRY_AFTER))
        except ValueError:
            seconds = DEFAULT_RETRY_AFTER

        seconds = min(seconds, MAX_COOLDOWN)
        logger.warning("rate limited by %s; pausing for %.0fs", response.url.host, seconds)

        loop = asyncio.get_running_loop()
        self._cooldown_until = max(self._cooldown_until, loop.time() + seconds)


profile_limiter = Limiter(PROFILE_RPS)
bulk_limiter = Limiter(BULK_RPS)


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    limits=httpx.Limits(
                        max_connections=MAX_CONNECTIONS,
                        max_keepalive_connections=MAX_CONNECTIONS,
                    ),
                    # Generous pool timeout: waiting for a free connection must not
                    # count against the per-request budget when many lookups queue.
                    timeout=httpx.Timeout(REQUEST_TIMEOUT, pool=30.0),
                )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _record_failure(uuid: str) -> None:
    if len(_failures) >= MAX_FAILURES_TRACKED:
        cutoff = _utcnow() - FAILURE_TTL
        for key in [k for k, at in _failures.items() if at < cutoff]:
            del _failures[key]
    _failures[uuid] = _utcnow()


def _recently_failed(uuid: str) -> bool:
    failed_at = _failures.get(uuid)
    return failed_at is not None and _utcnow() - failed_at < FAILURE_TTL


async def _send(limiter: Limiter, build) -> httpx.Response | None:
    """Retry a request until it says something conclusive, or attempts run out.

    Returns the response for any status the caller must interpret; None once
    only transient refusals remain.
    """
    client = await get_client()

    for _ in range(MAX_ATTEMPTS):
        await limiter.slot()
        try:
            response = await build(client)
        except httpx.RequestError:
            continue

        if response.status_code == 429:
            limiter.begin_cooldown(response)
            continue
        return response

    return None


async def name_for_uuid(uuid: str) -> str | None:
    """The username for a uuid, or None if upstream does not know it or would
    not say. None is not evidence of a missing account -- only a 404 is, and
    only that case is remembered.
    """
    if _recently_failed(uuid):
        return None

    response = await _send(profile_limiter, lambda c: c.get(PROFILE_URL.format(uuid)))
    if response is None:
        return None

    if response.status_code == 200:
        _failures.pop(uuid, None)
        return response.json().get(PROFILE_NAME_KEY)
    if response.status_code == 404:
        _record_failure(uuid)
    return None


async def uuids_for_names(names: list[str]) -> dict[str, str]:
    """Lowercased name -> current uuid, for up to BULK_SIZE names at a time.

    Names upstream does not recognise are absent from the result rather than
    null, so a miss and a rename are indistinguishable here -- the caller
    decides what that means.
    """
    if not names:
        return {}

    found: dict[str, str] = {}
    for i in range(0, len(names), BULK_SIZE):
        chunk = names[i:i + BULK_SIZE]
        response = await _send(bulk_limiter, lambda c, b=chunk: c.post(BULK_URL, json=b))
        if response is None or response.status_code != 200:
            if response is not None:
                logger.warning("bulk lookup failed: %s %s", response.status_code, response.text[:200])
            continue
        found.update({p["name"].lower(): p["id"] for p in response.json()})

    return found
