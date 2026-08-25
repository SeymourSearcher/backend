"""UUID -> Minecraft username lookups, cached in the players table.

Upstream calls share one pooled client. A page of auctions can need ~200
lookups; opening a client per call meant a fresh TLS handshake for each, and
unbounded concurrency that the upstream API degrades under. The pool cap is
what bounds concurrency here -- excess requests queue on it. See
MAX_CONNECTIONS for the measured tradeoff.
"""
import asyncio
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow as _utcnow
from app.models import Player

# Usernames change rarely, so a long TTL keeps cold pages off the upstream API.
CACHE_DURATION = timedelta(days=30)

# A uuid that failed is not retried for this long, so one unresolvable account
# cannot cost a request timeout on every single page load.
FAILURE_TTL = timedelta(minutes=15)
MAX_FAILURES_TRACKED = 10_000

API_URL = "https://api.ashcon.app/mojang/v2/user/{}"
REQUEST_TIMEOUT = 5.0

# Calibrated against the live API with a page-sized batch of 60 lookups:
# unbounded 7.6s (2 timeouts), 10 -> 1.37s, 25 -> 0.81s, 50 -> 5.13s (upstream
# degrades). Re-measure before changing; more concurrency is not better here.
MAX_CONNECTIONS = 25

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_failures: dict[str, datetime] = {}


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


async def fetch_username_from_api(uuid: str) -> str | None:
    if _recently_failed(uuid):
        return None

    client = await get_client()
    try:
        response = await client.get(API_URL.format(uuid))
        if response.status_code == 200:
            _failures.pop(uuid, None)
            return response.json().get("username")
    except httpx.RequestError:
        pass

    _record_failure(uuid)
    return None


async def get_player_names(db: AsyncSession, uuids: list[str]) -> dict[str, str]:
    clean_uuids = {uuid.replace("-", ""): uuid for uuid in uuids if uuid}
    if not clean_uuids:
        return {}

    result = await db.execute(select(Player).where(Player.uuid.in_(clean_uuids.keys())))
    players = {p.uuid: p for p in result.scalars().all()}

    names = {}
    to_fetch = []
    now = _utcnow()

    for clean, original in clean_uuids.items():
        player = players.get(clean)
        if player and now - player.last_updated < CACHE_DURATION:
            names[original] = player.username
        else:
            to_fetch.append((clean, original, player))

    if not to_fetch:
        return names

    fetched = await asyncio.gather(*[fetch_username_from_api(clean) for clean, _, _ in to_fetch])

    new_players = []
    for (clean, original, player), username in zip(to_fetch, fetched):
        if username:
            new_players.append({"uuid": clean, "username": username, "last_updated": now})
            names[original] = username
        elif player:
            names[original] = player.username

    if new_players:
        stmt = insert(Player).values(new_players)
        stmt = stmt.on_duplicate_key_update(
            username=stmt.inserted.username,
            last_updated=stmt.inserted.last_updated,
        )
        await db.execute(stmt)
        await db.commit()

    return names
