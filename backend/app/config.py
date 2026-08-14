from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://mp_user:mp_password@localhost:5432/marketplace_analytics"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "change-me-in-production"
    encryption_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@lru_cache()
def get_settings() -> Settings:
    return Settings()
