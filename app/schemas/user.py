from datetime import datetime
from pydantic import BaseModel, ConfigDict, field_validator


class UserResponse(BaseModel):
    id: int
    discord_id: str
    username: str
    avatar_hash: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("discord_id", mode="before")
    @classmethod
    def coerce_discord_id(cls, v):
        return str(v)

