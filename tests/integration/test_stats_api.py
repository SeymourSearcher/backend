"""GET /stats/, /stats/me and /stats/rankings.

None of the three had coverage, so StatsResponse/RankingResponse were never
checked against what the services actually produce. Asserts the contract --
key set, types, and the global-equals-sum-of-rankings invariant -- rather than
pinned counts, which depend on static/data.json.
"""
import pytest

from app.models import Seymour, User
from app.pieces import Piece
from app.schemas.stats import RankingResponse, StatsResponse

pytestmark = pytest.mark.integration

STAT_KEYS = set(StatsResponse.model_fields)
USER_KEYS = set(RankingResponse.model_fields) - STAT_KEYS


@pytest.fixture
async def inventories(db, user):
    """Two owners with different-sized inventories."""
    other = User(discord_id=99, username="rival", avatar_hash="abc")
    db.add(other)
    await db.flush()

    for owner, hexes in [(user, ["FFFF00", "FFFE00", "000000"]), (other, ["FFFF00"])]:
        for i, hex_color in enumerate(hexes):
            db.add(Seymour(user_id=owner.id, item_name=Piece(i % 4), hex_color=hex_color))
    await db.commit()
    return other


async def test_global_stats_match_the_declared_shape(client, inventories):
    body = (await client.get("/stats/")).json()

    assert set(body) == STAT_KEYS
    assert all(isinstance(v, int) for v in body.values())


async def test_custom_sets_is_not_returned(client, inventories):
    """The frontend used to declare it; the backend has never produced it."""
    assert "custom_sets" not in (await client.get("/stats/")).json()


async def test_my_stats_count_only_my_seymours(client, as_user, user, inventories):
    as_user(user)
    body = (await client.get("/stats/me")).json()

    assert set(body) == STAT_KEYS
    assert body["total_seymours"] == 3


async def test_my_stats_require_authentication(client, inventories):
    assert (await client.get("/stats/me")).status_code in (401, 403)


async def test_rankings_carry_stats_plus_user_fields(client, inventories):
    rankings = (await client.get("/stats/rankings")).json()

    assert len(rankings) == 2
    for row in rankings:
        assert set(row) == STAT_KEYS | USER_KEYS
        assert isinstance(row["discord_id"], str)

    by_name = {r["username"]: r for r in rankings}
    assert by_name["tester"]["total_seymours"] == 3
    assert by_name["rival"]["total_seymours"] == 1
    assert by_name["rival"]["avatar_hash"] == "abc"


async def test_global_stats_are_the_sum_of_the_rankings(client, inventories):
    rankings = (await client.get("/stats/rankings")).json()
    globals_ = (await client.get("/stats/")).json()

    for key in STAT_KEYS:
        assert globals_[key] == sum(r[key] for r in rankings), key


async def test_stats_are_zero_when_nobody_has_uploaded(client):
    body = (await client.get("/stats/")).json()

    assert set(body) == STAT_KEYS
    assert body["total_seymours"] == 0
