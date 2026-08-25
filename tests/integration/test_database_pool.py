"""Guard for the outage this suite was written after.

pool_pre_ping=True made every checkout of an *already-pooled* connection raise
TypeError on the pinned SQLAlchemy, so the app served traffic fine until the
pool warmed up and then 500'd. Any test that opens one session misses it
entirely -- the reuse is the whole point.

Asserted as behaviour rather than as "pre_ping is off", so this keeps passing
once SQLAlchemy is upgraded and pre_ping can be turned back on.
"""
import asyncio

import pytest
from sqlalchemy import text

from app.database import async_session, engine

pytestmark = pytest.mark.integration


async def test_sequential_checkouts_reuse_pooled_connections():
    for _ in range(8):
        async with async_session() as session:
            assert await session.scalar(text("SELECT 1")) == 1


async def test_concurrent_checkouts_then_reuse():
    async def query():
        async with async_session() as session:
            return await session.scalar(text("SELECT 1"))

    # Fill the pool, let everything return to it, then draw on it again.
    assert await asyncio.gather(*[query() for _ in range(10)]) == [1] * 10
    assert await asyncio.gather(*[query() for _ in range(10)]) == [1] * 10


async def test_pool_is_bounded_and_recycles():
    pool = engine.pool
    assert pool.size() > 0
    assert engine.pool._recycle > 0
    # Recycling has to stay well inside MySQL's wait_timeout or the pool hands
    # out sockets the server already closed.
    wait_timeout = await _wait_timeout()
    assert engine.pool._recycle < wait_timeout


async def _wait_timeout() -> int:
    async with async_session() as session:
        return int(await session.scalar(text("SELECT @@wait_timeout")))
