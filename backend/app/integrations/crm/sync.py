"""Run a sync: fetch changed records from the CRM and pass them through the standard importer."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.periods import org_today
from app.core.config import get_settings
from app.core.database import rls_bypass, set_tenant
from app.integrations.crm import base
from app.integrations.crm.hubspot import HubSpotConnector
from app.integrations.crm.salesforce import SalesforceConnector
from app.integrations.crm.sandbox import SandboxConnector
from app.integrations.crm.zoho import ZohoConnector
from app.models import ImportEntity, Integration, Organization, Role, SyncJob, User
from app.services import audit, importer

log = logging.getLogger(__name__)

CONNECTORS: dict[str, base.Connector] = {c.provider: c for c in (ZohoConnector(), HubSpotConnector(), SalesforceConnector(), SandboxConnector())}
# Ask for a little before the last start, so records saved while it ran aren't missed (re-reading is harmless).
CURSOR_OVERLAP = timedelta(minutes=5)
MAX_ERRORS = 200


def default_http() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def run_sync(db: Session, integration: Integration, trigger: str = "manual", http: httpx.Client | None = None,
             sleep: Callable[[float], None] | None = None) -> SyncJob:
    connector = CONNECTORS[integration.provider]
    set_tenant(db, integration.organization_id)
    org = db.get(Organization, integration.organization_id)
    started = datetime.now(timezone.utc)
    job = SyncJob(organization_id=org.id, integration_id=integration.id, trigger=trigger, status="running",
                  started_at=started, counts={}, errors=[])
    db.add(job)
    db.flush()

    def save_tokens(tokens: base.Tokens) -> None:
        integration.credentials = base.encrypt(tokens)

    owners = sorted(db.scalars(select(User.email).where(User.organization_id == org.id, User.deleted_at.is_(None),
                                                        User.role == Role.SALES_EXECUTIVE)))
    client = http or default_http()
    cursors = dict(integration.cursors or {})
    try:
        api = base.ApiClient(connector, base.decrypt(integration.credentials), integration.settings or {}, client,
                             on_refresh=save_tokens, **({"sleep": sleep} if sleep else {}),
                             context={"owner_emails": owners, "today": org_today(org.timezone)})
        for entity in connector.entities:
            since = datetime.fromisoformat(cursors[entity]) if entity in cursors else None
            rows = list(connector.fetch(api, entity, since))
            counts = {"fetched": len(rows)}
            if rows:
                result = importer.validate(db, org.id, ImportEntity(entity), rows)
                counts.update(importer.apply(db, org.id, result, source=f"sync:{integration.provider}"))
                counts["errors"] = result.error_row_count
                for e in result.errors:
                    if len(job.errors) < MAX_ERRORS:
                        job.errors.append({"entity": entity, **e})
                db.flush()  # later entities refer to these (orders to customers and products)
            job.counts = {**job.counts, entity: counts}
            job.records += len(rows)
            cursors[entity] = (started - CURSOR_OVERLAP).isoformat()
        job.status = "partial" if job.errors else "succeeded"
        integration.status = "connected"
        integration.last_success_at = started
        integration.last_error = None
        integration.cursors = cursors
    except base.AuthError as exc:
        db.rollback()
        job, integration = _after_rollback(db, job, integration)
        job.status, job.message = "failed", str(exc)
        integration.status, integration.last_error = "needs_reconnect", str(exc)
    except (base.ConnectorError, importer.ImportFileError) as exc:
        db.rollback()
        job, integration = _after_rollback(db, job, integration)
        job.status, job.message = "failed", str(exc)
        integration.status, integration.last_error = "error", str(exc)
    finally:
        if http is None:
            client.close()

    job.finished_at = datetime.now(timezone.utc)
    integration.last_sync_at = started
    audit.record(db, org.id, None, "integration.synced", "integration", integration.id,
                 {"provider": integration.provider, "status": job.status, "records": job.records, "trigger": trigger})
    db.commit()
    return job


def _after_rollback(db: Session, job: SyncJob, integration: Integration) -> tuple[SyncJob, Integration]:
    """A failed sync writes nothing from that run, but its job record and the error are kept."""
    set_tenant(db, job.organization_id)  # a rollback starts a new transaction
    integration = db.get(Integration, integration.id)
    failed = SyncJob(organization_id=job.organization_id, integration_id=integration.id, trigger=job.trigger,
                     status="failed", started_at=job.started_at, counts=job.counts, errors=job.errors, records=0)
    db.add(failed)
    return failed, integration


def sync_due(db: Session, now: datetime | None = None) -> list[SyncJob]:
    """Sync every working connection that hasn't synced within the interval (for a scheduler)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=get_settings().sync_interval_minutes)
    jobs = []
    with rls_bypass(db):  # every organisation's connections
        due = list(db.scalars(select(Integration).where(Integration.status.in_(("connected", "error")))))
    for integration in due:
        last = integration.last_sync_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or last < cutoff:
            jobs.append(run_sync(db, integration, trigger="scheduled"))
    return jobs
