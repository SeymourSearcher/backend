"""Fixtures backed by a throwaway MySQL.

MySQL rather than SQLite on purpose: the code under test leans on things SQLite
either lacks or silently ignores -- ROW_NUMBER() over a filtered subquery,
SELECT ... FOR UPDATE row locks, and ON DUPLICATE KEY UPDATE. A SQLite run would
pass while proving nothing about production.

Run with:
    docker run -d --name seymour-testdb -e MYSQL_ROOT_PASSWORD=test \
        -e MYSQL_DATABASE=seymour_test -p 3312:3306 mysql:8.4
    TEST_DATABASE_URL=mysql+aiomysql://root:test@127.0.0.1:3312/seymour_test pytest
"""
import os

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.integration

from app.database import Base  # noqa: E402
from app.dependencies import get_db, get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, pool_recycle=3600, pool_size=5, max_overflow=5)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def sessionmaker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(scope="session")
async def schema(engine):
    """Build the schema once for the run.

    This used to be per-test, and it was 97% of the suite: ~0.7s of DDL before
    each test, against ~0.02s of test. Emptying the tables gives the same
    isolation for a fraction of that -- the schema never varies between tests,
    only the rows do.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
async def clean_db(engine, schema):
    """Empty every table, so ordering and leftovers can never matter.

    DELETE rather than TRUNCATE: TRUNCATE drops and rebuilds the tablespace, and
    at 16 tables that cost ~0.34s per test even though nearly all of them were
    already empty. DELETE against an empty table touches no pages.

    Ids therefore keep climbing across the run instead of restarting. That is
    only safe while no test asserts a literal id, which is what the suite
    passing demonstrates -- they all capture ids from the rows they create.

    FOREIGN_KEY_CHECKS goes off for the duration because the loop's order is not
    a topological one, and is restored on the same connection before it returns
    to the pool.
    """
    async with engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for table in Base.metadata.sorted_tables:
                await conn.execute(table.delete())
        finally:
            await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


@pytest.fixture
async def db(clean_db, sessionmaker):
    async with sessionmaker() as session:
        yield session


@pytest.fixture
async def user(db):
    row = User(discord_id=1, username="tester", avatar_hash=None)
    db.add(row)
    await db.commit()
    return row


@pytest.fixture
async def other_user(db):
    row = User(discord_id=2, username="other", avatar_hash=None)
    db.add(row)
    await db.commit()
    return row


@pytest.fixture
async def redis():
    """In-process Redis, so the suite needs only the MySQL container."""
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def client(clean_db, sessionmaker, redis):
    """Anonymous client. Override get_current_user via `as_user` for authed calls."""
    async def override_db():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def as_user():
    """Authenticate the shared client as a given user for the rest of the test.

    Overrides both auth dependencies -- endpoints taking get_optional_user would
    otherwise stay anonymous, since the client never sends a real bearer token.

    The user is loaded from the request's own session, via the same get_db the
    endpoint gets. Opening a private session here instead would hand handlers a
    User attached to nothing they commit, so a handler that writes to it -- as
    upload and removal both do to last_uploaded -- would silently drop the write
    in tests while working in production.
    """
    from app.dependencies import get_current_user, get_optional_user

    def _login(row):
        user_id = row.id

        async def override(db: AsyncSession = Depends(get_db)):
            return await db.get(User, user_id)

        app.dependency_overrides[get_current_user] = override
        app.dependency_overrides[get_optional_user] = override

    return _login


@pytest.fixture(autouse=True)
def stub_names(monkeypatch):
    """Names come from the players table, which these fixtures don't populate."""
    async def fake(db, uuids):
        return {uuid: f"player-{uuid[:4]}" for uuid in uuids}

    monkeypatch.setattr("app.services.auction_queries.lookup_names", fake)
