import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import require_roles
from app.models import ImportEntity, ImportJob, ImportStatus, Role, User
from app.services import audit, importer

router = APIRouter(prefix="/imports", tags=["data import"])
can_import = require_roles(Role.SUPER_ADMIN, Role.ORG_ADMIN, Role.ANALYST)


class FieldInfo(BaseModel):
    name: str
    type: str
    required: bool


class ImportJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity: ImportEntity
    filename: str
    status: ImportStatus
    total_rows: int
    valid_rows: int
    error_rows: int
    column_mapping: dict
    warnings: list
    result: dict | None
    created_at: datetime


class PreviewOut(BaseModel):
    job: ImportJobOut
    unmapped_columns: list[str]
    errors: list[dict]
    sample: list[dict]


@router.get("/fields/{entity}", response_model=list[FieldInfo])
def import_fields(entity: ImportEntity, _: User = Depends(can_import)):
    """The fields a file for this entity can contain, for building a mapping screen."""
    return [FieldInfo(name=f.name, type=f.kind, required=f.required) for f in importer.SPECS[entity]]


@router.post("/{entity}/preview", response_model=PreviewOut, status_code=status.HTTP_201_CREATED)
async def preview_import(
    entity: ImportEntity,
    file: UploadFile = File(...),
    mapping: str | None = Form(default=None, description='Optional JSON: {"field": "File column"}'),
    user: User = Depends(can_import),
    db: Session = Depends(get_db),
):
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File is larger than {get_settings().max_upload_mb} MB")

    try:
        mapping_override = json.loads(mapping) if mapping else None
        if mapping_override is not None and not isinstance(mapping_override, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "mapping must be a JSON object") from None

    filename = (file.filename or "upload")[:255]
    try:
        raw_rows = importer.read_table(content, filename)
        result = importer.validate(db, user.organization_id, entity, raw_rows, mapping_override)
    except importer.ImportFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None

    job = ImportJob(
        organization_id=user.organization_id,
        created_by_id=user.id,
        entity=entity,
        filename=filename,
        status=ImportStatus.PREVIEWED,
        total_rows=result.total_rows,
        valid_rows=len(result.rows),
        error_rows=result.error_row_count,
        column_mapping=result.mapping,
        errors=result.errors[: importer.MAX_REPORTED_ERRORS],
        warnings=result.warnings,
        raw_rows=raw_rows,
    )
    db.add(job)
    db.commit()
    return PreviewOut(
        job=ImportJobOut.model_validate(job),
        unmapped_columns=result.unmapped_columns,
        errors=result.errors[:100],
        sample=importer.preview_rows(result),
    )


def _get_job(db: Session, user: User, job_id: uuid.UUID) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None or job.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    return job


@router.post("/{job_id}/confirm", response_model=ImportJobOut)
def confirm_import(job_id: uuid.UUID, user: User = Depends(can_import), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    if job.status != ImportStatus.PREVIEWED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This import is already {job.status.value}")

    try:
        result = importer.validate(db, user.organization_id, job.entity, job.raw_rows or [], job.column_mapping)
    except importer.ImportFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if not result.rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No valid rows to import")

    counts = importer.apply(db, user.organization_id, result)
    job.status = ImportStatus.COMPLETED
    job.valid_rows = len(result.rows)
    job.error_rows = result.error_row_count
    job.errors = result.errors[: importer.MAX_REPORTED_ERRORS]
    job.warnings = result.warnings
    job.result = counts
    job.raw_rows = None  # no need to keep the uploaded data once it is imported
    audit.record(db, user.organization_id, user.id, "import.completed", "import_job", job.id,
                 {"entity": job.entity.value, **counts})
    db.commit()
    return job


@router.get("", response_model=list[ImportJobOut])
def list_imports(user: User = Depends(can_import), db: Session = Depends(get_db)):
    return db.scalars(
        select(ImportJob)
        .where(ImportJob.organization_id == user.organization_id)
        .order_by(ImportJob.created_at.desc())
        .limit(100)
    ).all()


@router.get("/{job_id}/errors", response_model=list[dict])
def import_errors(job_id: uuid.UUID, user: User = Depends(can_import), db: Session = Depends(get_db)):
    return _get_job(db, user, job_id).errors
