import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TenantMixin, TimestampMixin, utcnow

ACTIVE_JOB_STATUSES = ("queued", "running")


class Job(IdMixin, TimestampMixin, TenantMixin, Base):
    """Work that takes too long for a web request (CRM syncs, model training, WhatsApp replies).

    Requests add a job and return at once; a worker runs it (app/worker.py). A dedupe key allows only
    one queued or running job per key, so repeated clicks and webhook retries don't pile up.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_queue", "status", "run_after"),
        Index("uq_jobs_active_dedupe", "dedupe_key", unique=True,
              postgresql_where=text("status IN ('queued', 'running')"),
              sqlite_where=text("status IN ('queued', 'running')")),
    )

    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | succeeded | failed
    dedupe_key: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)  # shown to the user, so never a stack trace
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class RefreshToken(IdMixin, TenantMixin, Base):
    """One row per refresh token issued (its id is the token's jti). Each can be used once.

    Using one returns a new token in the same family. Using an already-used one again (outside a
    short grace period for two tabs refreshing together) means it was copied: every session of
    that user is ended.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # signed out


class RateLimitCounter(Base):
    """Requests per key per fixed time window, shared by every server process."""

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
