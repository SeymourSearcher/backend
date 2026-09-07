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


class HexPrice(BaseModel):
    """Median ended-auction price for this colour at this tier.

    median is None when nothing comparable has sold, which is not the same as a
    price of zero.
    """
    median: float | None
    sales: int


class HexMatchResponse(BaseModel):
    target_name: str
    target_hex: str
    delta_e: float
    pieces: list[Piece]
    users_by_tier: HexUsersByTier
    price: HexPrice


class HexInfoResponse(BaseModel):
    hex_code: str
    # Set when the searched colour *is* a catalog colour. It is then absent from
    # top_matches, which lists only genuinely nearby items.
    exact_match: HexMatchResponse | None
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
