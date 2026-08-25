from pydantic import BaseModel, ConfigDict, field_validator

from app.pieces import Piece


class PublicUser(BaseModel):
    """The user fields safe to show alongside someone else's listing."""
    discord_id: str
    username: str
    avatar_hash: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("discord_id", mode="before")
    @classmethod
    def coerce_discord_id(cls, v):
        return str(v)


class SeymourBrief(BaseModel):
    """A Seymour as it appears nested inside another resource."""
    id: int
    item_name: Piece
    hex_color: str

    model_config = ConfigDict(from_attributes=True)


class MatchBrief(BaseModel):
    """The closest catalog colour to a Seymour."""
    target_name: str
    target_hex: str
    delta_e: float


class DetailResponse(BaseModel):
    """Acknowledgement body for endpoints with nothing to return.

    Deliberately not 204: the client reads every response with res.json(), which
    rejects on an empty body.
    """
    detail: str


class HealthResponse(BaseModel):
    status: str
