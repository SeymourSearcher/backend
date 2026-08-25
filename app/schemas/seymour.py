from pydantic import BaseModel, ConfigDict

from app.pieces import Piece
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


class MatchInfo(BaseModel):
    target_name: str
    target_hex: str
    delta_e: float
    missing: list[Piece] | None = None


class SeymourWithMatchResponse(BaseModel):
    id: int
    item_name: Piece
    hex_color: str
    match: MatchInfo | None = None


class UploadResponse(BaseModel):
    imported: int
    skipped: int
