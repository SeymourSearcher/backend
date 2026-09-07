from enum import Enum

from pydantic import BaseModel

from app.models.user_stats import STAT_KEYS

# Built from STAT_KEYS so the sortable columns stay the tallied columns.
RankingSort = Enum("RankingSort", {key: key for key in STAT_KEYS}, type=str)


class StatsResponse(BaseModel):
    stars: int
    t1: int
    t2: int
    t3: int
    words: int
    dupes: int
    total_sets: int
    dyed_sets: int
    total_seymours: int


class RankingResponse(StatsResponse):
    discord_id: str
    username: str
    avatar_hash: str | None = None


class FeaturedSetResponse(BaseModel):
    """One gallery tile: a four-piece set and who owns it. `hexes` is in
    app.pieces.Piece order."""
    discord_id: str
    username: str
    avatar_hash: str | None = None
    avg_delta_e: float
    hexes: list[str]
