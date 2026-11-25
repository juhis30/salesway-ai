import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import ADMIN_ROLES, Job, User

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    """Slow work (a CRM sync, retraining) runs in the background; poll this until it finishes."""

    id: uuid.UUID
    kind: str
    status: str  # queued | running | succeeded | failed
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: dict | None
    error: str | None


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    # Visible to whoever started it and to admins of the same organisation.
    if (job is None or job.organization_id != user.organization_id
            or (job.created_by_id != user.id and user.role not in ADMIN_ROLES)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
