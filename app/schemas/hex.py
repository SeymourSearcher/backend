from pydantic import BaseModel

from app.pieces import Piece


class HexUserResponse(BaseModel):
    discord_id: str
    username: str
    avatar_hash: str | None = None
    delta_e: float


class HexUsersByTier(BaseModel):
    t1: list[HexUserResponse]
    t2: list[HexUserResponse]
    t3: list[HexUserResponse]


class HexMatchResponse(BaseModel):
    target_name: str
    target_hex: str
    delta_e: float
    pieces: list[Piece]
    users_by_tier: HexUsersByTier


class HexInfoResponse(BaseModel):
    hex_code: str
    top_matches: list[HexMatchResponse]
