from pydantic import BaseModel

from app.pieces import Piece
from app.schemas.common import PublicUser


class HexUserResponse(PublicUser):
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
