"""Cold/warm latency for every endpoint /items and /hex call.

Deliberately not named test_*.py: it seeds prod-scale volumes (~106k seymour
rows, ~170k ended auctions), which takes minutes, so a plain `pytest` run must
not pick it up. Pass the path explicitly to run it:

    docker run --rm -v "$PWD:/src" -w /src \
        --add-host=host.docker.internal:host-gateway \
        -e TEST_DATABASE_URL=mysql+aiomysql://root:test@host.docker.internal:3312/seymour_test \
        seymour-backend-test pytest tests/bench/bench_endpoints.py -s

Cold means the Redis namespaces the call reads were just dropped -- what a user
hits after an auction sync or an upload. Warm is the cached path.
"""
import asyncio
import os
import random
import time

import fakeredis.aioredis
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.dependencies import get_current_user, get_db, get_optional_user, get_redis
from app.main import app
from app.models import EndedAuction, Seymour, User
from app.services.compute import KEY_ITEM_HEXES, UNIQUE_KEY_HEXES

OWNER_ROWS = 106_000
OWNER_COUNT = 32
COLLECTION = 350
AUCTIONS = 170_000
CHUNK = 5_000

SEARCH_HEX = "7F00FF"


def _holdings(rng, n):
    """(hex, piece) pairs drawn from the catalog, half of them nudged off it.

    Pieces come paired with their colour rather than picked at random, so armor
    sets actually complete -- otherwise the set filters measure an empty result.
    """
    catalog = list(KEY_ITEM_HEXES)
    out = []
    for _ in range(n):
        hex_code, piece = rng.choice(catalog)
        value = int(hex_code, 16)
        if rng.random() < 0.5:
            value ^= rng.randrange(0, 0x0F0F0F)
        out.append((f"{value & 0xFFFFFF:06X}", piece))
    return out


async def seed(engine, sessionmaker):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    rng = random.Random(7)
    async with sessionmaker() as db:
        users = [User(discord_id=i + 1, username=f"user{i}", avatar_hash=None)
                 for i in range(OWNER_COUNT)]
        db.add_all(users)
        await db.commit()
        me = users[0].id
        ids = [u.id for u in users]

        rows = []
        for i, (hex_color, piece) in enumerate(_holdings(rng, OWNER_ROWS)):
            # The measured user gets a normal-sized collection; the rest carry the table.
            owner = me if i < COLLECTION else rng.choice(ids[1:])
            rows.append({"user_id": owner, "hex_color": hex_color, "item_name": piece})
        for start in range(0, len(rows), CHUNK):
            await db.execute(Seymour.__table__.insert(), rows[start:start + CHUNK])
            await db.commit()

        auctions = []
        for i, (hex_color, piece) in enumerate(_holdings(rng, AUCTIONS)):
            target = rng.choice(UNIQUE_KEY_HEXES)
            de = rng.random() * 10
            tier = "t1" if de < 2 else "t2" if de < 5 else "t3"
            auctions.append({
                "auction_id": f"a{i}", "seller_uuid": f"s{i % 500}",
                "buyer_uuid": f"b{i % 500}", "price": rng.randrange(10_000, 500_000_000),
                "is_bin": rng.random() < 0.7, "item_uuid": f"i{i}",
                "hex_color": hex_color, "piece": piece,
                "timestamp": 1_700_000_000_000 + rng.randrange(0, 86_400_000 * 90),
                "target_hex_keys": target, "delta_e_keys": de, "tier_keys": tier,
                "target_hex_all": target, "delta_e_all": de, "tier_all": tier,
            })
        for start in range(0, len(auctions), CHUNK):
            await db.execute(EndedAuction.__table__.insert(), auctions[start:start + CHUNK])
            await db.commit()

        return me


async def timed(client, label, url, redis, results):
    await redis.flushall()
    start = time.perf_counter()
    cold = await client.get(url)
    cold_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    warm = await client.get(url)
    warm_ms = (time.perf_counter() - start) * 1000

    results.append((label, cold.status_code, cold_ms, warm_ms))
    print(f"{label:<44} {cold.status_code}  cold {cold_ms:8.1f}ms  warm {warm_ms:7.1f}ms",
          flush=True)


async def test_bench():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=5, max_overflow=5)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    print("\nseeding...", flush=True)
    start = time.perf_counter()
    user_id = await seed(engine, sessionmaker)
    print(f"seeded in {time.perf_counter() - start:.0f}s", flush=True)

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def override_db():
        async with sessionmaker() as session:
            yield session

    async def override_user():
        async with sessionmaker() as session:
            return await session.get(User, user_id)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_optional_user] = override_user

    results = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 timeout=300) as client:
        print("\n--- /items ---", flush=True)
        await timed(client, "GET /stats/me", "/stats/me", redis, results)
        await timed(client, "GET /stats/me/valuation", "/stats/me/valuation", redis, results)
        for f in ("all", "t1", "sets", "dyes", "stars", "dupes", "custom"):
            await timed(client, f"GET /seymours/?filter={f}",
                        f"/seymours/?filter={f}&limit=210", redis, results)
        for f in ("all", "t1", "sets", "dyes"):
            await timed(client, f"GET /seymours/sub-filters?filter={f}",
                        f"/seymours/sub-filters?filter={f}", redis, results)
        await timed(client, "GET /seymours/hex-codes/with-pieces",
                    "/seymours/hex-codes/with-pieces", redis, results)

        print("\n--- /hex ---", flush=True)
        await timed(client, f"GET /hex/{SEARCH_HEX}", f"/hex/{SEARCH_HEX}", redis, results)
        await timed(client, "GET /hex/../auction-stats",
                    f"/hex/{SEARCH_HEX}/auction-stats", redis, results)
        await timed(client, "GET /hex/../sales", f"/hex/{SEARCH_HEX}/sales?limit=8", redis, results)
        await timed(client, "GET /hex/../owners", f"/hex/{SEARCH_HEX}/owners?limit=5", redis, results)

        print("\n--- one cold /hex page view: all four calls at once ---", flush=True)
        await redis.flushall()
        paths = [f"/hex/{SEARCH_HEX}", f"/hex/{SEARCH_HEX}/auction-stats",
                 f"/hex/{SEARCH_HEX}/sales?limit=8", f"/hex/{SEARCH_HEX}/owners?limit=5"]
        start = time.perf_counter()
        await asyncio.gather(*(client.get(p) for p in paths))
        print(f"  concurrent (what the page does)          "
              f"{(time.perf_counter() - start) * 1000:8.1f}ms", flush=True)

        await redis.flushall()
        start = time.perf_counter()
        for p in paths:
            await client.get(p)
        print(f"  sequential (one rebuild, three hits)     "
              f"{(time.perf_counter() - start) * 1000:8.1f}ms", flush=True)

        print("\n--- /hex, hex_info already warm (the 2nd-4th calls of one page view) ---",
              flush=True)
        await redis.flushall()
        await client.get(f"/hex/{SEARCH_HEX}")
        for label, path in (
            ("auction-stats after hex_info", f"/hex/{SEARCH_HEX}/auction-stats"),
            ("sales after hex_info", f"/hex/{SEARCH_HEX}/sales?limit=8"),
            ("owners after hex_info", f"/hex/{SEARCH_HEX}/owners?limit=5"),
        ):
            start = time.perf_counter()
            r = await client.get(path)
            print(f"{label:<44} {r.status_code}  {(time.perf_counter() - start) * 1000:8.1f}ms",
                  flush=True)

    app.dependency_overrides.clear()
    await engine.dispose()

    print("\n--- slowest cold ---", flush=True)
    for label, _, cold, warm in sorted(results, key=lambda r: -r[2])[:8]:
        print(f"{cold:8.1f}ms  (warm {warm:6.1f}ms)  {label}", flush=True)

    await breakdown(sessionmaker, redis)
    await redis.aclose()


async def step(label, coro):
    start = time.perf_counter()
    value = await coro
    print(f"  {label:<40} {(time.perf_counter() - start) * 1000:8.1f}ms", flush=True)
    return value


def sync_step(label, fn, *args):
    start = time.perf_counter()
    value = fn(*args)
    print(f"  {label:<40} {(time.perf_counter() - start) * 1000:8.1f}ms", flush=True)
    return value


async def breakdown(sessionmaker, redis):
    """Where a cold /hex actually spends its time."""
    from app.repositories.seymour_repo import SeymourRepository
    from app.services import owner_hexes as oh
    from app.services.compute import find_top_hex_matches
    from app.services.hex_prices import price_estimates
    from app.services.owner_matches import find_owner_matches, tier_users

    print("\n--- cold /hex breakdown ---", flush=True)
    await redis.flushall()
    async with sessionmaker() as db:
        rows = await step("SeymourRepository.list_owner_hexes", SeymourRepository(db).list_owner_hexes())
        print(f"  ({len(rows)} rows)", flush=True)
        owners = sync_step("owner_hexes._build", oh._build, rows)
        payload = sync_step("owner_hexes._encode", oh._encode, owners)
        await step("OWNER_HEXES.set (json.dumps + redis)", oh.OWNER_HEXES.set(redis, payload))
        cached = await step("OWNER_HEXES.get (redis + json.loads)", oh.OWNER_HEXES.get(redis))
        sync_step("owner_hexes._decode", oh._decode, cached)

        # The join above ships username/discord_id/avatar_hash on all 106k rows
        # for 32 distinct users. Two queries carry the same information.
        from sqlalchemy import select
        from app.models import Seymour as S, User as U

        start = time.perf_counter()
        users = (await db.execute(select(U.id, U.username, U.discord_id, U.avatar_hash))).all()
        pairs = (await db.execute(select(S.user_id, S.hex_color, S.item_name))).all()
        print(f"  {'split: users + seymour columns':<40} "
              f"{(time.perf_counter() - start) * 1000:8.1f}ms  "
              f"({len(users)} users, {len(pairs)} rows)", flush=True)

        sync_step("find_top_hex_matches", find_top_hex_matches, SEARCH_HEX, 4, False, None)
        sync_step("tier_users (one match)", tier_users, owners, SEARCH_HEX)
        sync_step("find_owner_matches", find_owner_matches, owners, SEARCH_HEX)
        await redis.flushall()
        await step("price_estimates (4 pairs)", price_estimates(
            db, redis, [(h, "t1") for h in UNIQUE_KEY_HEXES[:4]], False, None))
