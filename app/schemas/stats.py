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


class CategoryValues(BaseModel):
    """Estimated worth per /items chip. No `custom`; see collection_value.py."""
    stars: int
    words: int
    dupes: int
    t1: int
    t2: int
    t3: int
    sets: int
    dyes: int


class CollectionValueResponse(BaseModel):
    """Estimated worth of a collection, in coins.

    `total` covers every item that could be priced; `unpriced` is how many could
    not, which is what makes `total` readable rather than merely large. The
    category figures overlap and do not sum to `total` -- an item can be a Star
    and T1 and in a set at once.
    """
    total: int
    priced: int
    unpriced: int
    categories: CategoryValues


class FeaturedSetResponse(BaseModel):
    """One gallery tile: a four-piece set and who owns it. `hexes` is in
    app.pieces.Piece order."""
    discord_id: str
    username: str
    avatar_hash: str | None = None
    avg_delta_e: float
    hexes: list[str]
