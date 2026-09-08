from pydantic import BaseModel

from app.pieces import Piece
from app.schemas.auction import AuctionStatsResponse, EndedAuctionResponse
from app.schemas.common import PublicUser


class HexSearchOwner(PublicUser):
    """One user's holdings of exactly this colour.

    Collapsed across pieces rather than listed per Seymour: a pattern row is
    already a colour, so repeating the user once per piece says nothing new.
    """
    pieces: list[Piece]
    quantity: int


class HexSearchResult(BaseModel):
    """A held colour the pattern matches, and its nearest catalog item.

    Every row has at least one owner -- the search filters the seymours snapshot
    rather than the colour space. target_name is null for a catalog colour with
    no set or dye name.
    """
    hex_code: str
    target_name: str | None
    target_hex: str
    delta_e: float
    tier: str | None
    owners: list[HexSearchOwner]
    owner_count: int
    # False for an anonymous caller, who owns nothing as far as this endpoint knows.
    owned_by_me: bool


class HexSearchResponse(BaseModel):
    """pattern_total is what the pattern denotes across the whole colour space
    (16 ** free slots); total is how many of those anyone actually holds, after
    the tier and piece filters. The gap between them is the point -- it says how
    rare the pattern is.

    mine_only reports what was actually applied, which is False for an anonymous
    caller even when the request asked for it.
    """
    pattern: str
    pattern_total: int
    total: int
    mine_only: bool
    limit: int
    offset: int
    items: list[HexSearchResult]


class PatternReference(BaseModel):
    """Which pattern and band the auction figures below were taken under.

    pattern is normalised -- upper-cased and '#'-stripped -- so it may differ
    from what was asked for. tier is null when every band was asked for.
    """
    pattern: str
    pattern_total: int
    tier: str | None


class PatternAuctionStatsResponse(PatternReference, AuctionStatsResponse):
    """Sales of the colours the pattern matches, not of the catalog colours they
    are near; see app/services/pattern_auctions.py."""


class PatternSalesResponse(PatternReference):
    """The sales PatternAuctionStatsResponse aggregates, newest first.

    A row's delta_e and tier are its own colour's distance to its own catalog
    target, which is what the result cards show too -- there is no searched
    colour here to measure against.
    """
    total: int
    items: list[EndedAuctionResponse]
