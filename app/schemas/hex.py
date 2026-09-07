from pydantic import BaseModel

from app.pieces import Piece
from app.schemas.common import PublicUser


class HexTierUser(PublicUser):
    """A user's best holding near one catalog colour."""
    delta_e: float


class HexUsersByTier(BaseModel):
    t1: list[HexTierUser]
    t2: list[HexTierUser]
    t3: list[HexTierUser]


class HexMatchResponse(BaseModel):
    target_name: str
    target_hex: str
    delta_e: float
    pieces: list[Piece]
    users_by_tier: HexUsersByTier


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
