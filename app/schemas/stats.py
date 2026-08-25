from pydantic import BaseModel


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
