"""Meta WhatsApp Cloud API: webhook signature check and payload parsing."""

import hashlib
import hmac
from dataclasses import dataclass


@dataclass
class Inbound:
    phone_number_id: str  # our business number (Meta's id), identifies the organisation
    from_phone: str       # sender in E.164 with "+"
    message_id: str       # wamid, used for idempotency
    type: str
    text: str | None


@dataclass
class StatusUpdate:
    message_id: str
    status: str  # sent | delivered | read | failed


def signature_valid(body: bytes, header: str | None, app_secret: str) -> bool:
    """Meta signs the raw body with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def sign(body: bytes, app_secret: str) -> str:
    """The header value Meta would send; used by tests and the local simulator script."""
    return "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()


def e164(digits: str) -> str:
    cleaned = "".join(ch for ch in digits if ch.isdigit())
    return f"+{cleaned}"


def parse_webhook(payload: dict) -> tuple[list[Inbound], list[StatusUpdate]]:
    messages, statuses = [], []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id", "")
            for m in value.get("messages") or []:
                kind = m.get("type", "")
                text = (m.get("text") or {}).get("body") if kind == "text" else None
                if kind == "interactive":  # quick-reply buttons carry their title as text
                    reply = (m.get("interactive") or {}).get("button_reply") or {}
                    text = reply.get("title")
                messages.append(Inbound(phone_number_id, e164(m.get("from", "")), m.get("id", ""), kind, text))
            for s in value.get("statuses") or []:
                statuses.append(StatusUpdate(s.get("id", ""), s.get("status", "")))
    return messages, statuses
