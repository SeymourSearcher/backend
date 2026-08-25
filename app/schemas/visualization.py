from pydantic import BaseModel, Field

from app.pieces import Piece

# The client asks for 200k when downloading the cube; anything above that is
# unrenderable in a browser anyway.
MAX_POINTS_LIMIT = 200_000


class VisualizationRequest(BaseModel):
    min_de: float = Field(0.0, ge=0)
    max_de: float = Field(5.0, ge=0)
    input_hex_codes: list[str] | None = None
    input_seymours: list[tuple[str, Piece]] | None = None
    include_dyes: bool = True
    max_points: int = Field(100_000, gt=0, le=MAX_POINTS_LIMIT)


class VisualizationStats(BaseModel):
    count: int
    total: int
    percentage: float
