from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

# Get the directory where this config file lives
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    
    discord_client_id: str
    discord_client_secret: str
    discord_redirect_uri: str
    
    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = BASE_DIR / ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()

