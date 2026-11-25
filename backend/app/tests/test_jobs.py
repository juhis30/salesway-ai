"""The background job queue: retries, crash recovery, clean-up."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import ImportJob, ImportStatus, Job, Organization
from app.services import job_handlers, jobs
from app.tests.conftest import register_org, upload

CUSTOMERS = "customer_code,customer_name\nC1,Gupta Stores\n"


def org_of(db_session, name):
    return db_session.scalar(select(Organization).where(Organization.name == name))


def test_unexpected_failures_are_retried_then_reported_safely(client, db_session, monkeypatch):
    register_org(client, "Acme", "admin@acme.in")
    org = org_of(db_session, "Acme")
    calls = []

    def boom(db, job):
        calls.append(job.attempts)
        raise RuntimeError("connection string postgres://secret@db leaked in a message")

    monkeypatch.setitem(job_handlers.HANDLERS, "crm_sync", job_handlers.Handler(boom, max_attempts=2))
    job = jobs.enqueue(db_session, org.id, "crm_sync", {})
    db_session.commit()

    jobs.run_pending(db_session)
    assert calls == [1] and job.status == "queued"  # retried later, not at once
    job.run_after = datetime.now(timezone.utc)
    db_session.flush()
    jobs.run_pending(db_session)
    assert calls == [1, 2] and job.status == "failed"
    assert "secret" not in job.error and str(job.id) in job.error  # the user sees a reference, not internals


def test_expected_failures_are_not_retried(client, db_session, monkeypatch):
    register_org(client, "Acme", "admin@acme.in")
    org = org_of(db_session, "Acme")

    def refuse(db, job):
        raise jobs.JobFailed("This connection was removed before the sync started.")

    monkeypatch.setitem(job_handlers.HANDLERS, "crm_sync", job_handlers.Handler(refuse, max_attempts=3))
    job = jobs.enqueue(db_session, org.id, "crm_sync", {})
    db_session.commit()
    jobs.run_pending(db_session)
    assert job.status == "failed" and job.attempts == 1 and job.error.startswith("This connection was removed")


def test_maintenance_recovers_cut_off_jobs_and_deletes_old_data(client, db_session):
    admin = register_org(client, "Acme", "admin@acme.in")
    org = org_of(db_session, "Acme")
    now = datetime.now(timezone.utc)
    long_ago = now - timedelta(days=40)

    cut_off = Job(organization_id=org.id, kind="crm_sync", status="running", attempts=1, max_attempts=2,
                  started_at=now - timedelta(hours=2))
    gave_up = Job(organization_id=org.id, kind="ml_train", status="running", attempts=1, max_attempts=1,
                  started_at=now - timedelta(hours=2))
    finished = Job(organization_id=org.id, kind="ml_train", status="succeeded", finished_at=long_ago)
    db_session.add_all([cut_off, gave_up, finished])
    upload(client, admin, "customers", CUSTOMERS, confirm=False)
    preview = db_session.scalar(select(ImportJob))
    preview.created_at = long_ago
    db_session.flush()

    counts = jobs.maintenance(db_session)
    assert counts["stale_jobs"] == 2 and counts["old_jobs"] == 1 and counts["expired_previews"] == 1
    assert cut_off.status == "queued"  # it gets another go
    assert gave_up.status == "failed" and "restarted" in gave_up.error
    db_session.refresh(preview)
    assert preview.status == ImportStatus.EXPIRED and preview.raw_rows is None
    confirm = client.post(f"/api/v1/imports/{preview.id}/confirm", headers=admin)
    assert confirm.status_code == 409 and "Upload the file again" in confirm.json()["detail"]


def test_api_docs_are_off_in_production():
    from app.core.config import Settings

    assert Settings(environment="development").api_docs_enabled
    assert not Settings(environment="production").api_docs_enabled
    assert Settings(environment="production", api_docs=True).api_docs_enabled
