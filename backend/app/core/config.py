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

    # Assistant language model: "mock" (rule-based, no key needed) or "anthropic".
    # The anthropic provider reads ANTHROPIC_API_KEY (or an `ant auth login` profile).
    llm_provider: str = "mock"
    anthropic_model: str = "claude-opus-5-5"

    # WhatsApp: "mock" records outgoing messages instead of sending; "meta" uses the Cloud API.
    whatsapp_provider: str = "mock"
    whatsapp_verify_token: str = ""   # chosen by you, entered in the Meta app's webhook settings
    whatsapp_app_secret: str = ""     # Meta app secret, used to verify webhook signatures
    whatsapp_access_token: str = ""   # system-user token with whatsapp_business_messaging
    whatsapp_api_version: str = "v21.0"

    assistant_rate_limit_per_minute: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
