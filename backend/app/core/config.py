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
    # Optional separate login for migrations (the table owner) when DATABASE_URL is a restricted
    # role that row-level security applies to. See docs/security.md.
    migration_database_url: str = ""

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    jwt_secret: str = DEFAULT_JWT_SECRET
    # To rotate JWT_SECRET without signing everyone out: move the old value here, set a new
    # JWT_SECRET, and remove the old one once refresh tokens signed with it have expired (7 days).
    jwt_previous_secrets: list[str] = []
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    cors_origins: list[str] = ["http://localhost:3000"]
    # Interactive API docs at /docs. On by default only in development.
    api_docs: bool | None = None

    @property
    def api_docs_enabled(self) -> bool:
        return self.api_docs if self.api_docs is not None else self.environment == "development"

    # Run queued jobs (CRM syncs, model training, WhatsApp replies) in a thread of the API process.
    # Turn off when a separate worker runs `python -m app.worker`.
    run_worker: bool = True
    # Uploaded files that were previewed but never imported are deleted after this many days.
    import_preview_days: int = 7

    # Per IP address; shared by all server processes (stored in the database).
    login_rate_limit_per_minute: int = 30
    register_rate_limit_per_hour: int = 10

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

    # Optional error tracking (sentry.io); nothing is sent when empty.
    sentry_dsn: str = ""

    # --- CRM connections (read-only) ---------------------------------------------------------
    # Public addresses: OAuth sends people back to the API's callback, which then returns them to the web app.
    public_api_url: str = "http://localhost:8000"
    public_web_url: str = "http://localhost:3000"
    # Encrypts stored CRM credentials. Any long random string; falls back to JWT_SECRET if unset.
    # To rotate: put the new key first and keep the old one after a comma ("new,old"). Each sync
    # re-encrypts with the new key; remove the old one once every connection has synced.
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
