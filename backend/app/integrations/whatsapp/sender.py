"""Outgoing WhatsApp messages: the real Cloud API, or a mock that records them locally."""

import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from functools import lru_cache

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)
MAX_TEXT = 4096  # WhatsApp's limit for a text message


class SendError(RuntimeError):
    pass


def _fit(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


class Sender:
    name = "base"

    def send_text(self, phone_number_id: str, to: str, text: str) -> str:
        """Send and return the provider's message id."""
        raise NotImplementedError


class MockSender(Sender):
    """Records messages instead of sending them. Used for local development and tests."""

    name = "mock"

    def __init__(self) -> None:
        self.outbox: deque[dict] = deque(maxlen=200)

    def send_text(self, phone_number_id, to, text):
        message_id = f"mock.{uuid.uuid4().hex}"
        self.outbox.append({"id": message_id, "phone_number_id": phone_number_id, "to": to, "text": _fit(text),
                            "at": datetime.now(timezone.utc).isoformat()})
        return message_id


class MetaCloudSender(Sender):
    name = "meta"

    def __init__(self, access_token: str, api_version: str):
        if not access_token:
            raise SendError("WHATSAPP_ACCESS_TOKEN is not set")
        self.client = httpx.Client(
            base_url=f"https://graph.facebook.com/{api_version}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )

    def send_text(self, phone_number_id, to, text):
        body = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"body": _fit(text), "preview_url": False},
        }
        for attempt in range(3):
            try:
                response = self.client.post(f"/{phone_number_id}/messages", json=body)
            except httpx.HTTPError as exc:
                error = f"network error: {exc}"
            else:
                if response.status_code < 300:
                    return response.json()["messages"][0]["id"]
                error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in (429, 500, 502, 503, 504):
                    break  # not worth retrying (bad number, outside the 24-hour window, bad token...)
            time.sleep(0.5 * 2**attempt)
        log.error("WhatsApp send failed: %s", error)
        raise SendError(error)


@lru_cache
def get_sender() -> Sender:
    settings = get_settings()
    if settings.whatsapp_provider == "meta":
        return MetaCloudSender(settings.whatsapp_access_token, settings.whatsapp_api_version)
    return MockSender()
