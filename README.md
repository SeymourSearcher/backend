# Seymour Searcher API

The backend for [Seymour Searcher](https://github.com/SeymourFinder/frontend), a collection tracker and marketplace for Seymour's Special Armor on Hypixel SkyBlock. Players upload an export of their dyed armor pieces. The API matches each hex colour against the known armor sets by delta E, works out which sets a collection could complete, and runs a buy and sell order board between players. A separate worker keeps a live copy of Seymour auctions from the Hypixel API.

Built with FastAPI, SQLAlchemy (async, MySQL), Redis, NumPy and SciPy, plus a small Rust extension for the custom-set search.

```mermaid
flowchart LR
    web[Next.js frontend] -->|REST, JWT| api[API<br/>FastAPI]
    api --> db[(MySQL)]
    api --> cache[(Redis)]
    api -.->|loads at startup| npy[Precomputed<br/>colour tables]
    worker[Worker<br/>app.worker] --> db
    worker -->|auctions| hypixel[Hypixel API]
    worker -->|player names| mojang[Mojang API]
    api -->|OAuth| discord[Discord]
```

## Features

- Collection upload in JSON, Markdown, quoted or pipe-delimited export formats, with each piece matched against every target colour
- Set finder for complete, near-complete and custom (colour-consistent) sets across a collection
- A page for each of the 16.7M hex colours, with its closest matches, owners, auction stats and past sales
- Pattern search: fix some digits, wildcard others with `?`, or bind repeated digits with letters G to Z (`21????`, `XXYYZZ`)
- Buy orders by set or hex pattern, sell orders by piece, and matching between the two
- Auction browser with bid history, peak-time stats and top sales
- Discord sign-in

## Requirements

- Python 3.12
- MySQL 8
- Redis
- A Discord application for OAuth ([developer portal](https://discord.com/developers/applications))
- Optional: a Rust toolchain and `maturin` for the `seymour_sets` extension. Without it the custom-set search falls back to a NumPy path that is about 10x slower. See [rust/seymour_sets/README.md](rust/seymour_sets/README.md).

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # then fill in the values below
alembic upgrade head
python run.py                   # API on http://127.0.0.1:8000, docs at /docs
python -m app.worker            # auction and player-name sync, in a second shell
```

Run exactly one worker. The sync loop has no cross-process lock.

## Configuration

Settings are read from the environment or from `.env` in the repository root.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | SQLAlchemy URL, e.g. `mysql+aiomysql://user:pass@localhost:3306/seymour_finder` |
| `REDIS_URL` | no | Defaults to `redis://localhost:6379` |
| `JWT_SECRET` | yes | Signing key for session tokens. Generate one per environment with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `JWT_EXPIRE_MINUTES` | no | Token lifetime, default 30 |
| `DISCORD_CLIENT_ID` | yes | From your Discord application |
| `DISCORD_CLIENT_SECRET` | yes | From your Discord application |
| `DISCORD_REDIRECT_URI` | yes | The frontend's `/auth/callback` URL, registered with Discord |
| `FRONTEND_URL` | no | Allowed CORS origin, default `http://localhost:3000` |

## Precomputed colour tables

Hex lookups are O(1) because the nearest target for each of the 16,777,216 RGB colours is computed ahead of time and stored as `.npy` files in `app/static/`. The tables are not committed. The Docker image builds them, and for local development you generate them once (about 15 seconds, 270 MB) and again whenever the catalog or SciPy changes:

```bash
python scripts/precompute_hex_matches.py
python scripts/precompute_hex_order.py
```

The ordering tables are only exact against the distances the installed SciPy produces, which is why they are built in the environment that serves them. Without them the API still works but falls back to slower per-request tree queries.

## Catalog

`app/static/data.json` mirrors the checklist from the [SeymourAnalyzer](https://github.com/Schnerry/SeymourAnalyzerFabric) mod by Schnerry (GPL-3.0): target colour names, hexes, categories and page order. The `sets` grouping is maintained here.

To resync, bump `UPSTREAM_REF` in `scripts/sync_checklist.py` and run it. It prints the follow-up steps. New upstream items are reported, not assigned to sets automatically.

## Tests

```bash
scripts/run_tests.sh              # unit + integration on the host interpreter
scripts/run_tests.sh --docker     # same suite inside the image, against the pinned dependencies
scripts/run_tests.sh tests/unit   # extra arguments go to pytest
```

The script starts a throwaway MySQL container on port 3312. The `--docker` run is the one that counts, because the host interpreter can drift from `requirements.txt`. See [tests/README.md](tests/README.md).

## Project layout

```
app/
  routers/          HTTP handlers: validate, call a service, respond
  services/         business logic and the colour maths
  repositories/     every database query
  models/           SQLAlchemy models
  schemas/          Pydantic request and response models
  static/           catalog and precomputed colour tables
  worker.py         auction and player sync entrypoint
alembic/            migrations
rust/seymour_sets/  Rust kernel for the custom-set search (PyO3)
scripts/            precompute, backfill, sync and benchmark scripts
tests/              unit and integration suites
```

## Deployment

The Dockerfile has three targets built on one base image:

| Target | Runs |
|---|---|
| `api` (default) | `uvicorn` on port 8000, with a `/health` check |
| `worker` | `python -m app.worker`, one replica only |
| `test` | `pytest` against the pinned dependencies |

Per-client rate limits read `CF-Connecting-IP` and the forwarded headers. Those are only trustworthy when the container is reachable solely through your reverse proxy and CDN, so firewall the origin to them.

## Contributing

Issues and pull requests are welcome. Run the `--docker` suite before opening a pull request, and add a migration under `alembic/versions` for any model change.

## License

[GPL-3.0-or-later](LICENSE). The catalog data comes from SeymourAnalyzer, which is also GPL-3.0.
