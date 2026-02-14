from pydantic import BaseModel


class DiscordCallback(BaseModel):
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

