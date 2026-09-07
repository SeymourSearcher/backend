from pydantic import BaseModel

from app.pieces import Piece
from app.schemas.common import PublicUser


class HexSearchOwner(PublicUser):
    """One user's holdings of exactly this colour.

    Collapsed across pieces rather than listed per Seymour: a pattern row is
    already a colour, so repeating the user once per piece says nothing new.
    """
    pieces: list[Piece]
    quantity: int


class HexSearchResult(BaseModel):
    """A colour the pattern matches, and its nearest catalog item.

    target_name is null for a catalog colour with no set or dye name. owners is
    empty for most rows -- the search spans the whole colour space, so ownership
    is an annotation rather than a filter.
    """
    hex_code: str
    target_name: str | None
    target_hex: str
    delta_e: float
    tier: str | None
    owners: list[HexSearchOwner]
    owner_count: int


class HexSearchResponse(BaseModel):
    """pattern_total is what the pattern denotes (16 ** free slots); total is
    what survived the tier and ownership filters and is what offset pages over.
    They diverge whenever a filter is on, and the UI shows both."""
    pattern: str
    pattern_total: int
    total: int
    limit: int
    offset: int
    items: list[HexSearchResult]
