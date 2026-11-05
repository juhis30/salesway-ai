import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import require_roles
from app.integrations.whatsapp import service
from app.integrations.whatsapp.meta import Inbound, parse_webhook, signature_valid
from app.integrations.whatsapp.sender import MockSender, get_sender
from app.models import ADMIN_ROLES, Role, User, WhatsAppAccount
from app.services import audit

log = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp"])
admin_only = require_roles(*ADMIN_ROLES)

# --- Webhook (called by Meta) ------------------------------------------------------------------


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse, include_in_schema=False)
def verify_webhook(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
):
    """Meta's one-time subscription check: echo the challenge if the verify token matches."""
    expected = get_settings().whatsapp_verify_token
    if mode == "subscribe" and expected and token == expected:
        return challenge
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed")


@router.post("/webhooks/whatsapp", include_in_schema=False)
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    body = await request.body()
    if settings.whatsapp_app_secret:
        if not signature_valid(body, request.headers.get("X-Hub-Signature-256"), settings.whatsapp_app_secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid signature")
    elif settings.environment != "development":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "WHATSAPP_APP_SECRET is not configured")
    else:
        log.warning("Accepting an unsigned WhatsApp webhook (development only)")

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from None

    messages, statuses = parse_webhook(payload)
    sender = get_sender()
    # Processed inline for simplicity. Answers take well under Meta's timeout with the rule-based
    # assistant; with an LLM in production, queue these instead. Duplicates are safe either way.
    for inbound in messages:
        account = service.account_for(db, inbound.phone_number_id)
        if account is None:
            log.warning("WhatsApp message for unknown phone_number_id %s ignored", inbound.phone_number_id)
            continue
        try:
            service.handle_message(db, account.organization_id, account.phone_number_id, inbound, sender)
        except Exception:
            db.rollback()
            log.exception("Failed to handle WhatsApp message %s", inbound.message_id)
    for update in statuses:
        service.apply_status(db, update)
    db.commit()
    # Always 200 for a valid delivery, or Meta keeps retrying it.
    return {"status": "ok"}


# --- Admin: configuration and simulator ---------------------------------------------------------


class AccountIn(BaseModel):
    phone_number_id: str = Field(min_length=3, max_length=64, pattern=r"^[\w-]+$")
    display_phone: str | None = Field(default=None, max_length=32)


class AccountOut(AccountIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    enabled: bool


class WhatsAppUser(BaseModel):
    id: uuid.UUID
    full_name: str
    role: Role
    phone: str | None
    whatsapp_opt_in: bool


class SettingsOut(BaseModel):
    provider: str
    webhook_path: str
    verify_token_set: bool
    app_secret_set: bool
    access_token_set: bool
    accounts: list[AccountOut]
    users: list[WhatsAppUser]


class SimulateIn(BaseModel):
    user_id: uuid.UUID
    text: str = Field(min_length=1, max_length=1000)


class SimulateOut(BaseModel):
    to: str
    reply: str


@router.get("/whatsapp/settings", response_model=SettingsOut)
def whatsapp_settings(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    s = get_settings()
    org = user.organization_id
    return SettingsOut(
        provider=s.whatsapp_provider,
        webhook_path="/api/v1/webhooks/whatsapp",
        verify_token_set=bool(s.whatsapp_verify_token),
        app_secret_set=bool(s.whatsapp_app_secret),
        access_token_set=bool(s.whatsapp_access_token),
        accounts=db.scalars(select(WhatsAppAccount).where(WhatsAppAccount.organization_id == org)).all(),
        users=[
            WhatsAppUser(id=u.id, full_name=u.full_name, role=u.role, phone=u.phone, whatsapp_opt_in=u.whatsapp_opt_in)
            for u in db.scalars(
                select(User).where(User.organization_id == org, User.deleted_at.is_(None), User.is_active).order_by(User.full_name)
            )
        ],
    )


@router.post("/whatsapp/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def add_account(body: AccountIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    account = WhatsAppAccount(organization_id=user.organization_id, **body.model_dump())
    db.add(account)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "This phone number id is already connected") from None
    audit.record(db, user.organization_id, user.id, "whatsapp.account_added", "whatsapp_account", account.id)
    db.commit()
    return account


@router.delete("/whatsapp/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_account(account_id: uuid.UUID, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    account = db.get(WhatsAppAccount, account_id)
    if account is None or account.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    db.delete(account)
    audit.record(db, user.organization_id, user.id, "whatsapp.account_removed", "whatsapp_account", account_id)
    db.commit()


@router.post("/whatsapp/simulate", response_model=SimulateOut)
def simulate(body: SimulateIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Send a message *as* one of your users through the full WhatsApp path, without Meta.

    The reply is captured, never sent, and the user's own permissions apply to the answer.
    """
    target = db.get(User, body.user_id)
    if target is None or target.organization_id != admin.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not target.phone:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add a WhatsApp number to this user first")

    capture = MockSender()
    inbound = Inbound(phone_number_id="simulator", from_phone=target.phone, message_id=f"sim.{uuid.uuid4().hex}",
                      type="text", text=body.text)
    reply = service.handle_message(db, admin.organization_id, "simulator", inbound, capture)
    return SimulateOut(to=target.phone, reply=reply or "")
