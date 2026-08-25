"""Environment setup that must happen before any `app.*` import.

app.config.Settings is instantiated at import time by app.database and
app.dependencies, and app.database builds the engine from it. Fixing the
environment here is what keeps a unit test run from ever pointing at the real
database named in the developer's .env.
"""
import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "mysql+aiomysql://unused:unused@127.0.0.1:1/unused"
)
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DISCORD_CLIENT_ID", "test-client-id")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DISCORD_REDIRECT_URI", "http://localhost/auth/callback")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379")

import pytest  # noqa: E402


def pytest_collection_modifyitems(config, items):
    """Report the DB-backed tests as skipped rather than quietly not running them."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL is not set; see tests/README.md")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
