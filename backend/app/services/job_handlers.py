"""What each kind of job does. A handler runs with the job's organisation set (row-level security)
and returns a small JSON-able result that the browser shows when it polls the job."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.models import Integration, Job, Organization


@dataclass(frozen=True)
class Handler:
    run: Callable[[Session, Job], dict]
    # Unexpected crashes are retried this many times in all. Expected failures (a CRM refusing the
    # credentials, say) are recorded by the handler itself and not retried.
    max_attempts: int


def crm_sync(db: Session, job: Job) -> dict:
    from app.integrations.crm.sync import run_sync
    from app.services.jobs import JobFailed

    integration = db.get(Integration, uuid.UUID(job.payload["integration_id"]))
    if integration is None:
        raise JobFailed("This connection was removed before the sync started.")
    sync = run_sync(db, integration, trigger=job.payload.get("trigger", "manual"))
    return {"sync_job_id": str(sync.id), "status": sync.status, "records": sync.records, "message": sync.message}


def ml_train(db: Session, job: Job) -> dict:
    from app.ml.train import train_organization
    from app.services import audit

    org = db.get(Organization, job.organization_id)
    summary = train_organization(db, org)
    audit.record(db, org.id, job.created_by_id, "ml.trained", "organization", org.id,
                 {k: v for k, v in summary.items() if isinstance(v, str)})
    return jsonable_encoder(summary)


def whatsapp_message(db: Session, job: Job) -> dict:
    from app.integrations.whatsapp import service
    from app.integrations.whatsapp.meta import Inbound
    from app.integrations.whatsapp.sender import get_sender

    inbound = Inbound(**job.payload["inbound"])
    # Safe to retry: a message already answered is recognised by its WhatsApp id and skipped.
    reply = service.handle_message(db, job.organization_id, inbound.phone_number_id, inbound, get_sender())
    return {"answered": reply is not None}


HANDLERS: dict[str, Handler] = {
    "crm_sync": Handler(crm_sync, max_attempts=2),
    "ml_train": Handler(ml_train, max_attempts=1),
    "whatsapp_message": Handler(whatsapp_message, max_attempts=3),
}
