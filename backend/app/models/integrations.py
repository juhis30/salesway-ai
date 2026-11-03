import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TenantMixin, TimestampMixin, utcnow


class Integration(IdMixin, TimestampMixin, TenantMixin, Base):
    """A connection from one organisation to one external system (Zoho, HubSpot, Salesforce...).

    Read-only: SaleSway copies data in and never writes back.
    status: connected | needs_reconnect (the CRM rejected our credentials) | error.
    """

    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("organization_id", "provider"),)

    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="connected")
    account_label: Mapped[str | None] = mapped_column(String(200))  # e.g. the CRM's org or portal name
    # Fernet-encrypted JSON of the OAuth tokens; never returned by the API.
    credentials: Mapped[str | None] = mapped_column(Text)
    # Non-secret provider details: Zoho api_domain, HubSpot portal id, Salesforce instance URL.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Per entity, the time the last successful sync started: the next one asks only for changes since.
    cursors: Mapped[dict] = mapped_column(JSON, default=dict)
    connected_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class SyncJob(IdMixin, TenantMixin, Base):
    __tablename__ = "sync_jobs"

    integration_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("integrations.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # manual | scheduled | webhook
    status: Mapped[str] = mapped_column(String(16))   # running | succeeded | partial | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Per entity: fetched, created, updated, skipped, errors.
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)  # first 200 row problems
    message: Mapped[str | None] = mapped_column(Text)
    records: Mapped[int] = mapped_column(Integer, default=0)
