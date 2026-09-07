from pydantic import BaseModel

from app.pieces import Piece
from app.schemas.common import PublicUser


class HexMatchResponse(BaseModel):
    target_name: str
    target_hex: str
    delta_e: float
    pieces: list[Piece]
    owner_count: int


class HexInfoResponse(BaseModel):
    hex_code: str
    top_matches: list[HexMatchResponse]


class HexOwnerMatch(PublicUser):
    """One holding: a user's piece whose colour is near the searched hex.

    Identical holdings are collapsed, so quantity may exceed 1.
    """
    piece: Piece
    hex_color: str
    delta_e: float
    quantity: int


class HexOwnersResponse(BaseModel):
    total: int
    max_delta_e: float
    items: list[HexOwnerMatch]
