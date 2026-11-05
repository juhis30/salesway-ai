"""Handle WhatsApp messages: identify the organisation and user, answer, reply, record."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.orchestrator import Reply, answer, record_exchange
from app.core.database import rls_bypass, set_tenant
from app.ai.schema import Intent, Interpretation
from app.integrations.whatsapp.meta import Inbound, StatusUpdate
from app.integrations.whatsapp.sender import Sender, SendError
from app.models import Conversation, Message, User, WhatsAppAccount
from app.services import audit

log = logging.getLogger(__name__)

NOT_REGISTERED = ("This number isn't registered with SaleSway. "
                  "Ask your SaleSway admin to add your WhatsApp number to your profile.")
NOT_TEXT = "I can only read text messages for now. Please type your question."
OPTED_OUT = "You've been unsubscribed from SaleSway briefings and alerts. You can still ask me questions any time. Send START to subscribe again."
OPTED_IN = "You're subscribed to SaleSway briefings and alerts. Send STOP any time to unsubscribe."


def account_for(db: Session, phone_number_id: str) -> WhatsAppAccount | None:
    """Which organisation owns this business number (a lookup across organisations)."""
    with rls_bypass(db):
        return db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.phone_number_id == phone_number_id, WhatsAppAccount.enabled))


def already_processed(db: Session, message_id: str) -> bool:
    return bool(message_id) and db.scalar(select(Message.id).where(Message.channel_message_id == message_id)) is not None


def _system_reply(text: str) -> Reply:
    return Reply(text=text, intent=Intent.HELP, interpretation=Interpretation(intent=Intent.HELP), facts=None, provider="system")


def _conversation(db: Session, user: User) -> Conversation:
    """WhatsApp is one continuous thread per user."""
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.channel == "whatsapp")
        .order_by(Conversation.last_message_at.desc())
        .limit(1)
    )
    if conversation is None:
        conversation = Conversation(organization_id=user.organization_id, user_id=user.id, channel="whatsapp", title="WhatsApp")
        db.add(conversation)
        db.flush()
    return conversation


def handle_message(db: Session, organization_id: uuid.UUID, phone_number_id: str, inbound: Inbound, sender: Sender) -> str | None:
    """Answer one inbound message. Returns the reply text (None if it was a duplicate)."""
    set_tenant(db, organization_id)
    if already_processed(db, inbound.message_id):
        log.info("Duplicate WhatsApp message %s ignored", inbound.message_id)
        return None

    # Users are looked up only inside the organisation that owns the receiving number.
    user = db.scalar(
        select(User).where(
            User.organization_id == organization_id,
            User.phone == inbound.from_phone,
            User.is_active,
            User.deleted_at.is_(None),
        )
    )
    if user is None:
        audit.record(db, organization_id, None, "whatsapp.unknown_sender", details={"phone_ending": inbound.from_phone[-4:]})
        db.commit()
        _send(sender, phone_number_id, inbound.from_phone, NOT_REGISTERED)
        return NOT_REGISTERED

    conversation = _conversation(db, user)
    text = (inbound.text or "").strip()
    command = text.lower()
    if inbound.type not in ("text", "interactive") or not text:
        reply = _system_reply(NOT_TEXT)
    elif command in ("stop", "unsubscribe"):
        user.whatsapp_opt_in = False
        audit.record(db, organization_id, user.id, "whatsapp.opt_out", "user", user.id)
        reply = _system_reply(OPTED_OUT)
    elif command in ("start", "subscribe"):
        user.whatsapp_opt_in = True
        user.whatsapp_opt_in_at = datetime.now(timezone.utc)
        audit.record(db, organization_id, user.id, "whatsapp.opt_in", "user", user.id)
        reply = _system_reply(OPTED_IN)
    else:
        reply = answer(db, user, conversation, text)

    stored = record_exchange(db, user, conversation, text or f"[{inbound.type}]", reply, inbound_id=inbound.message_id or None)
    sent_id = _send(sender, phone_number_id, user.phone, reply.text)
    stored.delivery_status = "sent" if sent_id else "failed"
    stored.channel_message_id = sent_id  # the outgoing wamid, so delivery receipts can find it
    db.commit()
    return reply.text


def _send(sender: Sender, phone_number_id: str, to: str, text: str) -> str | None:
    try:
        return sender.send_text(phone_number_id, to, text)
    except SendError:
        return None


def apply_status(db: Session, update: StatusUpdate) -> None:
    """Delivery receipts (sent / delivered / read / failed) for messages we sent."""
    if not update.message_id:
        return
    with rls_bypass(db):  # a receipt doesn't say which organisation it's for
        message = db.scalar(select(Message).where(Message.channel_message_id == update.message_id, Message.role == "assistant"))
    if message is not None:
        set_tenant(db, message.organization_id)
        message.delivery_status = update.status
        db.flush()
