from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.pieces import Piece
from app.schemas.fields import HexColor

# The client asks for 200k when downloading the cube; anything above that is
# unrenderable in a browser anyway.
MAX_POINTS_LIMIT = 200_000

# One kd-tree query per input, on a thread from the shared pool, for a caller
# that never authenticates -- so the list length is the bound on what a single
# request can cost. Well above any real collection.
MAX_INPUTS = 5_000

HexInputs = Annotated[list[HexColor], Field(max_length=MAX_INPUTS)]
SeymourInputs = Annotated[list[tuple[HexColor, Piece]], Field(max_length=MAX_INPUTS)]


class VisualizationRequest(BaseModel):
    min_de: float = Field(0.0, ge=0)
    max_de: float = Field(5.0, ge=0)
    input_hex_codes: HexInputs | None = None
    input_seymours: SeymourInputs | None = None
    include_dyes: bool = True
    max_points: int = Field(100_000, gt=0, le=MAX_POINTS_LIMIT)

    @model_validator(mode="after")
    def validate_band(self):
        if self.max_de < self.min_de:
            raise ValueError("max_de must be greater than or equal to min_de")
        return self


class VisualizationStats(BaseModel):
    count: int
    total: int
    percentage: float
