import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TenantMixin, TimestampMixin, utcnow


class Conversation(IdMixin, TimestampMixin, TenantMixin, Base):
    """A chat thread between one user and the assistant, on the web or WhatsApp."""

    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # "web" | "whatsapp"
    title: Mapped[str | None] = mapped_column(String(200))
    # What the last question was about, so follow-ups like "and last month?" can be understood.
    state: Mapped[dict | None] = mapped_column(JSON)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Message(IdMixin, TenantMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(48))
    # Audit trail: interpretation, the facts the answer was built from, provider, grounding result.
    meta: Mapped[dict | None] = mapped_column(JSON)
    # WhatsApp message id (wamid); unique so a webhook delivered twice is processed once.
    channel_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    delivery_status: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WhatsAppAccount(IdMixin, TimestampMixin, TenantMixin, Base):
    """Maps a WhatsApp Business phone number to the organisation that owns it."""

    __tablename__ = "whatsapp_accounts"

    phone_number_id: Mapped[str] = mapped_column(String(64), unique=True)  # Meta's id, not the number
    display_phone: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
