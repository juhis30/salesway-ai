from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    """All configuration comes from environment variables (or backend/.env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "SaleSway AI"
    environment: str = "development"

    # SQLite for the local demo; switch to postgresql+psycopg://... without code changes.
    database_url: str = "sqlite:///./salesway.db"

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    cors_origins: list[str] = ["http://localhost:3000"]

    max_upload_mb: int = 10

    # "mock" until a real provider (bedrock, anthropic, sarvam, ...) is configured.
    llm_provider: str = "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
