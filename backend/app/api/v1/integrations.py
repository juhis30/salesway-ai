import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import require_roles
from app.integrations.crm import base
from app.integrations.crm.hubspot import verify_signature
from app.integrations.crm.sync import CONNECTORS, default_http, run_sync, sync_due
from app.models import ADMIN_ROLES, Integration, SyncJob, User
from app.services import audit

log = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])
admin_only = require_roles(*ADMIN_ROLES)
STATE_MINUTES = 10


class IntegrationOut(BaseModel):
    id: uuid.UUID
    provider: str
    status: str
    account_label: str | None
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime


class ProviderOut(BaseModel):
    provider: str
    label: str
    description: str
    entities: list[str]
    not_synced: str | None
    configured: bool
    missing_settings: list[str]
    integration: IntegrationOut | None


class JobOut(BaseModel):
    id: uuid.UUID
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    counts: dict
    errors: list
    message: str | None
    records: int


def _redirect_uri(provider: str) -> str:
    return f"{get_settings().public_api_url.rstrip('/')}/api/v1/integrations/{provider}/callback"


def _own_integration(db: Session, user: User, integration_id: uuid.UUID) -> Integration:
    integration = db.get(Integration, integration_id)
    if integration is None or integration.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
    return integration


@router.get("/integrations", response_model=list[ProviderOut])
def list_integrations(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    mine = {i.provider: i for i in db.scalars(select(Integration).where(Integration.organization_id == user.organization_id))}
    return [
        ProviderOut(
            provider=c.provider, label=c.label, description=c.description, entities=list(c.entities),
            not_synced=c.not_synced, configured=not c.missing_settings(), missing_settings=c.missing_settings(),
            integration=IntegrationOut.model_validate(mine[c.provider], from_attributes=True) if c.provider in mine else None,
        )
        for c in CONNECTORS.values()
    ]


@router.post("/integrations/{provider}/connect")
def connect(provider: str, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Start connecting: returns the CRM's sign-in page (OAuth), or connects the sandbox directly."""
    connector = CONNECTORS.get(provider)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown system")
    if connector.missing_settings():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{connector.label} isn't set up on this server yet: set {', '.join(connector.missing_settings())}.")
    if not connector.uses_oauth:
        _save_connection(db, user, provider, connector.exchange_code(None, "", ""))
        return {"connected": True}
    state = jwt.encode({"type": "oauth_state", "provider": provider, "org": str(user.organization_id),
                        "user": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(minutes=STATE_MINUTES)},
                       get_settings().jwt_secret, algorithm="HS256")
    return {"authorize_url": connector.authorize_url(state, _redirect_uri(provider))}


def _save_connection(db: Session, user: User, provider: str, connection: base.Connection) -> Integration:
    integration = db.scalar(select(Integration).where(Integration.organization_id == user.organization_id,
                                                      Integration.provider == provider))
    if integration is None:
        integration = Integration(organization_id=user.organization_id, provider=provider, cursors={})
        db.add(integration)
    integration.credentials = base.encrypt(connection.tokens)
    integration.settings = connection.settings
    integration.account_label = connection.account_label
    integration.status, integration.last_error = "connected", None
    integration.connected_by_id = user.id
    integration.cursors = {}  # a fresh connection (maybe to a different account) starts with a full sync
    audit.record(db, user.organization_id, user.id, "integration.connected", "integration", provider,
                 {"account": connection.account_label})
    db.commit()
    return integration


@router.get("/integrations/{provider}/callback", include_in_schema=False)
def oauth_callback(provider: str, state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    """The CRM sends the browser here after sign-in. The signed state says which organisation asked."""
    web = get_settings().public_web_url.rstrip("/")
    try:
        claims = jwt.decode(state, get_settings().jwt_secret, algorithms=["HS256"])
        if claims.get("type") != "oauth_state" or claims.get("provider") != provider:
            raise jwt.InvalidTokenError("wrong state")
    except jwt.InvalidTokenError:
        return _back(web, error="The sign-in link expired or was not started here. Try again.")
    if error or not code:
        return _back(web, error=error or "Sign-in was cancelled.")

    user = db.get(User, uuid.UUID(claims["user"]))
    if user is None or str(user.organization_id) != claims["org"] or not user.is_active:
        return _back(web, error="Your account changed during sign-in. Try again.")
    connector = CONNECTORS[provider]
    try:
        with default_http() as http:
            connection = connector.exchange_code(http, code, _redirect_uri(provider))
    except base.ConnectorError as exc:
        return _back(web, error=str(exc))
    _save_connection(db, user, provider, connection)
    return _back(web, connected=provider)


def _back(web: str, **params: str) -> RedirectResponse:
    return RedirectResponse(f"{web}/integrations?{urlencode(params)}")


@router.post("/integrations/{integration_id}/sync", response_model=JobOut)
def sync_now(integration_id: uuid.UUID, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    integration = _own_integration(db, user, integration_id)
    return run_sync(db, integration, trigger="manual")


@router.get("/integrations/{integration_id}/jobs", response_model=list[JobOut])
def jobs(integration_id: uuid.UUID, limit: int = Query(default=20, le=100), user: User = Depends(admin_only),
         db: Session = Depends(get_db)):
    integration = _own_integration(db, user, integration_id)
    return db.scalars(select(SyncJob).where(SyncJob.integration_id == integration.id)
                      .order_by(SyncJob.started_at.desc()).limit(limit)).all()


@router.delete("/integrations/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(integration_id: uuid.UUID, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Forget the credentials. Data already synced stays in SaleSway."""
    integration = _own_integration(db, user, integration_id)
    audit.record(db, user.organization_id, user.id, "integration.disconnected", "integration", integration.id,
                 {"provider": integration.provider})
    db.delete(integration)
    db.commit()


@router.post("/integrations/sync-due", include_in_schema=False)
def scheduled_sync(x_sync_token: str = Header(default=""), db: Session = Depends(get_db)):
    """Called by a scheduler (e.g. GitHub Actions every hour) with the SYNC_CRON_TOKEN."""
    expected = get_settings().sync_cron_token
    if not expected or x_sync_token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
    done = sync_due(db)
    return {"synced": len(done), "statuses": [j.status for j in done]}


@router.post("/webhooks/hubspot", include_in_schema=False)
async def hubspot_webhook(request: Request, db: Session = Depends(get_db)):
    """HubSpot tells us records changed; sync that portal's connection (changed records only)."""
    secret = get_settings().hubspot_client_secret
    body = await request.body()
    if not secret or not verify_signature(secret, "POST", str(request.url), body,
                                          request.headers.get("X-HubSpot-Request-Timestamp", ""),
                                          request.headers.get("X-HubSpot-Signature-v3", "")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid signature")
    try:
        portals = {str(e.get("portalId")) for e in json.loads(body)}
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid payload") from None
    synced = 0
    for integration in db.scalars(select(Integration).where(Integration.provider == "hubspot")):
        if (integration.settings or {}).get("portal_id") in portals and integration.status != "needs_reconnect":
            run_sync(db, integration, trigger="webhook")
            synced += 1
    return {"synced": synced}
