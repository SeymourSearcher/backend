from pydantic import BaseModel, ConfigDict

from app.pieces import Piece
from app.schemas.common import MatchBrief, SeymourBrief
from app.schemas.fields import HexColor


class SeymourBase(BaseModel):
    item_name: Piece
    hex_color: str


class SeymourCreate(SeymourBase):
    # Narrowed on input only; responses echo whatever is stored, including
    # lower-case rows written before this constraint existed.
    hex_color: HexColor


class SeymourResponse(SeymourBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class MatchInfo(MatchBrief):
    missing: list[Piece] | None = None


class SeymourWithMatchResponse(SeymourBrief):
    match: MatchInfo | None = None


class UploadResponse(BaseModel):
    imported: int
    skipped: int


class SubFilterOption(BaseModel):
    """One /items dropdown option. `name` is the sub_filter key, not a label."""
    name: str
    count: int


class RemovalResponse(BaseModel):
    """What clearing a collection actually took with it.

    sell_orders_cancelled is reported because it is the part nobody expects:
    the listings go by FK cascade, not by anything the caller asked for.
    """
    removed: int
    sell_orders_cancelled: int
