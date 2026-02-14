from datetime import datetime
from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    id: int
    discord_id: int
    username: str
    avatar_hash: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

