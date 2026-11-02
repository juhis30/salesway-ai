"""WhatsApp webhook: verification, signatures, tenant routing, idempotency, opt-out, receipts."""

import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.integrations.whatsapp.meta import sign
from app.integrations.whatsapp.sender import get_sender
from app.models import Message, User
from app.tests.conftest import auth_headers, register_org

SECRET = "test-app-secret"
RAVI_PHONE = "+919800000001"


@pytest.fixture
def wa(client, sales_org, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "whatsapp_app_secret", SECRET)
    monkeypatch.setattr(settings, "whatsapp_verify_token", "verify-me")
    outbox = get_sender().outbox
    outbox.clear()

    admin = sales_org["admin"]
    assert client.post("/api/v1/whatsapp/accounts", headers=admin, json={"phone_number_id": "PNID1", "display_phone": "+91 22 1234 5678"}).status_code == 201
    client.patch(f"/api/v1/users/{sales_org['ravi']['id']}", headers=admin, json={"phone": RAVI_PHONE})
    return {**sales_org, "outbox": outbox}


def payload(text=None, sender=RAVI_PHONE, message_id="wamid.1", phone_number_id="PNID1", statuses=None):
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_number_id}}
    if text is not None:
        value["messages"] = [{"from": sender.lstrip("+"), "id": message_id, "type": "text", "text": {"body": text}}]
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


def post(client, body: dict, secret=SECRET):
    raw = json.dumps(body).encode()
    return client.post("/api/v1/webhooks/whatsapp", content=raw,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign(raw, secret)})


def test_webhook_verification(client, wa):
    ok = client.get("/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"})
    assert (ok.status_code, ok.text) == (200, "12345")
    bad = client.get("/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
    assert bad.status_code == 403


def test_signature_required(client, wa):
    raw = json.dumps(payload("hi")).encode()
    assert client.post("/api/v1/webhooks/whatsapp", content=raw).status_code == 403
    assert post(client, payload("hi"), secret="wrong-secret").status_code == 403
    assert len(wa["outbox"]) == 0


def test_question_is_answered_once(client, wa, db_session):
    assert post(client, payload("my sales in October 2026")).status_code == 200
    assert len(wa["outbox"]) == 1
    reply = wa["outbox"][0]
    assert reply["to"] == RAVI_PHONE and reply["phone_number_id"] == "PNID1"
    assert "Revenue: *₹4,000*" in reply["text"]

    # Meta retries deliveries: the same message id must not be answered twice.
    assert post(client, payload("my sales in October 2026")).status_code == 200
    assert len(wa["outbox"]) == 1

    stored = db_session.scalars(select(Message).order_by(Message.created_at)).all()
    assert [(m.role, m.channel_message_id) for m in stored] == [("user", "wamid.1"), ("assistant", reply["id"])]
    assert stored[1].delivery_status == "sent"


def test_delivery_receipts(client, wa, db_session):
    post(client, payload("my sales in October 2026"))
    outbound_id = wa["outbox"][0]["id"]
    post(client, payload(statuses=[{"id": outbound_id, "status": "read"}]))
    assistant = db_session.scalar(select(Message).where(Message.role == "assistant"))
    assert assistant.delivery_status == "read"


def test_unknown_number_gets_registration_help(client, wa):
    post(client, payload("hi", sender="+919999999999"))
    assert wa["outbox"][0]["to"] == "+919999999999"
    assert "isn't registered" in wa["outbox"][0]["text"]


def test_numbers_route_to_their_own_organisation(client, wa):
    # Another company's WhatsApp number can't be used to reach Ravi's data.
    other = register_org(client, "Rival Co", "admin@rival.in")
    client.post("/api/v1/whatsapp/accounts", headers=other, json={"phone_number_id": "PNID2"})
    post(client, payload("my sales in October 2026", message_id="wamid.x", phone_number_id="PNID2"))
    assert "isn't registered" in wa["outbox"][0]["text"]

    # A number nobody connected is ignored entirely.
    post(client, payload("hi", message_id="wamid.y", phone_number_id="UNKNOWN"))
    assert len(wa["outbox"]) == 1


def test_stop_and_start(client, wa, db_session):
    post(client, payload("START", message_id="wamid.a"))
    ravi = db_session.scalar(select(User).where(User.email == "ravi@acme.in"))
    assert ravi.whatsapp_opt_in is True and ravi.whatsapp_opt_in_at is not None
    post(client, payload("stop", message_id="wamid.b"))
    db_session.refresh(ravi)
    assert ravi.whatsapp_opt_in is False
    assert "unsubscribed" in wa["outbox"][-1]["text"]


def test_simulator(client, wa):
    admin = wa["admin"]
    response = client.post("/api/v1/whatsapp/simulate", headers=admin, json={"user_id": wa["ravi"]["id"], "text": "how is Sita doing in October 2026?"})
    assert response.status_code == 200
    # The simulated user's own permissions apply, not the admin's.
    assert "You can only see numbers for yourself" in response.json()["reply"]
    assert len(wa["outbox"]) == 0  # simulator replies are captured, never sent

    no_phone = client.post("/api/v1/whatsapp/simulate", headers=admin, json={"user_id": wa["sita"]["id"], "text": "hi"})
    assert no_phone.status_code == 422
    ravi = auth_headers(client, "ravi@acme.in")
    assert client.post("/api/v1/whatsapp/simulate", headers=ravi, json={"user_id": wa["ravi"]["id"], "text": "hi"}).status_code == 403
