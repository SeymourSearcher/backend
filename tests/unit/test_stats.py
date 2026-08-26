"""STAT_KEYS names the user_stats columns, and the response schema declares the
same nine fields. Nothing else forces the two to agree, so pin them here.
"""
from app.models.user_stats import STAT_KEYS, UserStats
from app.schemas.stats import RankingResponse, StatsResponse
from app.services.stats import get_stats


def test_stat_keys_match_the_response_schema():
    assert set(STAT_KEYS) == set(StatsResponse.model_fields)


def test_stat_keys_match_the_stored_columns():
    stored = set(UserStats.__table__.columns.keys()) - {"user_id", "computed_at"}
    assert set(STAT_KEYS) == stored


def test_ranking_response_is_stats_plus_user_fields():
    extra = set(RankingResponse.model_fields) - set(STAT_KEYS)
    assert extra == {"discord_id", "username", "avatar_hash"}


def test_get_stats_returns_exactly_the_declared_keys():
    assert set(get_stats([("FFFF00", 0), ("AABBCC", 1)])) == set(STAT_KEYS)
