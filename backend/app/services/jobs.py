"""A small job queue in Postgres (or SQLite locally), for work too slow for a web request.

A request calls enqueue() and returns the job at once; the browser polls GET /jobs/{id}. A worker
(app/worker.py: a thread in the API process, or a separate process) claims queued jobs with
SELECT ... FOR UPDATE SKIP LOCKED, so several workers never run the same job, and runs the
handler registered for its kind in app/services/job_handlers.py.

No Redis or Celery needed: the database we already have is the queue.
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import rls_bypass, set_tenant
from app.models import ACTIVE_JOB_STATUSES, ImportJob, ImportStatus, Job, RateLimitCounter, RefreshToken

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=30)  # a running job this old was cut off (restart, crash)
RETRY_DELAY = timedelta(seconds=30)
KEEP_FINISHED = timedelta(days=30)

_wake = threading.Event()  # set by enqueue() so an idle worker starts at once


class JobFailed(Exception):
    """A failure whose message is safe and useful to show the user."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=timezone.utc) if moment and moment.tzinfo is None else moment


def enqueue(db: Session, organization_id: uuid.UUID, kind: str, payload: dict | None = None, *,
            dedupe_key: str | None = None, created_by_id: uuid.UUID | None = None) -> Job:
    """Add a job (the caller commits). With a dedupe key, returns the job already waiting or running."""
    from app.services.job_handlers import HANDLERS

    if dedupe_key:
        existing = db.scalar(select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE_JOB_STATUSES)))
        if existing is not None:
            return existing
    job = Job(organization_id=organization_id, kind=kind, payload=payload or {}, dedupe_key=dedupe_key,
              created_by_id=created_by_id, max_attempts=HANDLERS[kind].max_attempts, run_after=_now())
    try:
        with db.begin_nested():  # a unique index allows one active job per dedupe key
            db.add(job)
    except IntegrityError:  # another request queued the same work a moment ago
        return db.scalar(select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE_JOB_STATUSES)))
    wake()
    return job


def claim(db: Session) -> Job | None:
    """Take the oldest job that is due, marking it running. Other workers skip rows locked here."""
    with rls_bypass(db):  # jobs of every organisation
        job = db.scalar(
            select(Job).where(Job.status == "queued", Job.run_after <= _now())
            .order_by(Job.run_after, Job.created_at).limit(1).with_for_update(skip_locked=True)
        )
        if job is not None:
            job.status, job.started_at, job.attempts = "running", _now(), job.attempts + 1
        db.commit()
    return job


def run(db: Session, job: Job) -> Job:
    """Run one claimed job and record how it ended."""
    from app.services.job_handlers import HANDLERS

    job_id, organization_id = job.id, job.organization_id
    set_tenant(db, organization_id)
    try:
        result = HANDLERS[job.kind].run(db, job)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
        db.rollback()
        set_tenant(db, organization_id)
        job = db.get(Job, job_id)
        if isinstance(exc, JobFailed):
            job.error = str(exc)
        else:
            log.exception("job %s (%s) failed", job_id, job.kind)
            job.error = f"Something went wrong on our side. Reference: {job_id}"
        if job.attempts < job.max_attempts and not isinstance(exc, JobFailed):
            job.status, job.run_after = "queued", _now() + RETRY_DELAY * job.attempts
        else:
            job.status, job.finished_at = "failed", _now()
        db.commit()
        return job
    job = db.get(Job, job_id)
    job.status, job.result, job.error, job.finished_at = "succeeded", result, None, _now()
    db.commit()
    return job


def run_pending(db: Session, limit: int | None = None) -> int:
    """Run due jobs one after another until none are left (or `limit` ran). Returns how many ran."""
    ran = 0
    while limit is None or ran < limit:
        job = claim(db)
        if job is None:
            break
        run(db, job)
        ran += 1
    return ran


def wake() -> None:
    _wake.set()


def wait_for_work(timeout: float) -> None:
    _wake.wait(timeout)
    _wake.clear()


def maintenance(db: Session) -> dict:
    """Housekeeping: requeue jobs cut off mid-run, and delete data past its retention period."""
    now = _now()
    counts = {}
    with rls_bypass(db):
        stale = list(db.scalars(select(Job).where(Job.status == "running", Job.started_at < now - STALE_AFTER)))
        for job in stale:
            if job.attempts < job.max_attempts:
                job.status, job.run_after = "queued", now
            else:
                job.status, job.finished_at = "failed", now
                job.error = "This job stopped before it finished (the server restarted). Please try again."
        counts["stale_jobs"] = len(stale)
        counts["old_jobs"] = db.execute(delete(Job).where(Job.status.in_(("succeeded", "failed")),
                                                          Job.finished_at < now - KEEP_FINISHED)).rowcount
        counts["expired_previews"] = db.execute(
            update(ImportJob)
            .where(ImportJob.status == ImportStatus.PREVIEWED,
                   ImportJob.created_at < now - timedelta(days=get_settings().import_preview_days))
            .values(status=ImportStatus.EXPIRED, raw_rows=None)
        ).rowcount
        counts["expired_refresh_tokens"] = db.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < now - timedelta(days=1))).rowcount
        counts["old_rate_limit_counters"] = db.execute(
            delete(RateLimitCounter).where(RateLimitCounter.window_start < now - timedelta(days=1))).rowcount
        db.commit()
    return counts
