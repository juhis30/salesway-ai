from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    """All configuration comes from environment variables (or backend/.env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "SaleSway AI"
    environment: str = "development"

    # SQLite for the local demo. A Postgres URL as hosts give it (postgres://... or
    # postgresql://..., e.g. from Neon) is used as-is with the psycopg driver.
    database_url: str = "sqlite:///./salesway.db"

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

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

    # --- CRM connections (read-only) ---------------------------------------------------------
    # Public addresses: OAuth sends people back to the API's callback, which then returns them to the web app.
    public_api_url: str = "http://localhost:8000"
    public_web_url: str = "http://localhost:3000"
    # Encrypts stored CRM credentials. Any long random string; falls back to JWT_SECRET if unset.
    credentials_key: str = ""
    # Lets an external scheduler (e.g. GitHub Actions) trigger due syncs; disabled when empty.
    sync_cron_token: str = ""
    sync_interval_minutes: int = 60

    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    zoho_accounts_url: str = "https://accounts.zoho.in"  # .com / .eu / .com.au ... for other data centres
    zoho_api_version: str = "v7"
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_login_url: str = "https://login.salesforce.com"  # https://test.salesforce.com for sandboxes
    salesforce_api_version: str = "v62.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
