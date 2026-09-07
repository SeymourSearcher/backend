from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path

# Get the directory where this config file lives
BASE_DIR = Path(__file__).resolve().parent.parent

# Deliberately not a setting: an env var here is an alg-confusion switch, and
# there is no deployment that wants a different one.
JWT_ALGORITHM = "HS256"


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_expire_minutes: int = 30
    
    discord_client_id: str
    discord_client_secret: str
    discord_redirect_uri: str
    
    redis_url: str = "redis://localhost:6379"
    
    frontend_url: str = "http://localhost:3000"

    # extra="ignore" because the default is to forbid: a key retired from this
    # class (JWT_ALGORITHM) still lives in every deployed environment, and boot
    # must not fail on it.
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

