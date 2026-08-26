# Tests

```bash
scripts/run_tests.sh              # everything, host interpreter
scripts/run_tests.sh --docker     # everything, inside the image (authoritative)
scripts/run_tests.sh tests/unit   # trailing args are passed to pytest
```

The script starts a throwaway MySQL container (`seymour-testdb`, port 3312) and
leaves it running between invocations. `docker rm -f seymour-testdb` to reset.

## Layout

`tests/unit` needs nothing but the interpreter. It covers pure logic: tier
bucketing, the hex/delta-E helpers, WHERE-clause construction (asserted against
compiled SQL), price-bucket assembly, and the username client's pooling and
failure-TTL behaviour.

`tests/integration` needs `TEST_DATABASE_URL` and skips itself without one. It
runs the FastAPI app over a real schema via `httpx.ASGITransport`, with the
schema dropped and recreated per test.

## Why MySQL and not SQLite

The code under test depends on things SQLite lacks or silently ignores:
`ROW_NUMBER()` over a filtered subquery (the price percentiles),
`SELECT ... FOR UPDATE` (the sell-order race guard), and
`ON DUPLICATE KEY UPDATE` (the username cache upsert). A SQLite run would pass
while proving nothing about production.

## Why `--docker` is the run that counts

The host interpreter drifts from `requirements.txt` — at the time of writing, by
ten packages including numpy across a major version and SQLAlchemy across the
release that fixes `pool_pre_ping` on aiomysql.

That drift is not hypothetical. `pool_pre_ping=True` shipped and took the API
down; it had been tested locally on SQLAlchemy 2.0.49, where it works, against a
production pin of 2.0.36, where it raises `TypeError` on every reuse of a pooled
connection. `tests/integration/test_database_pool.py` covers exactly that path
and reproduces the failure — but only under `--docker`. On the host it passes.

Either keep the local environment aligned to the pins, or treat `--docker` as
the gate before shipping.

## Notes for adding tests

- Both loop scopes are session-scoped in `pytest.ini`; a session-scoped engine
  cannot serve tests running on a different loop.
- `app/config.py` is instantiated at import time, so `tests/conftest.py` fixes
  the environment before any `app.*` import. Do not import `app` above it.
- Anything calling `resolve_names` needs it stubbed, or the test hits the live
  upstream API. `lookup_names` is pure SQL and is safe unstubbed.
