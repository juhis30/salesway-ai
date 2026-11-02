import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    db: Session,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    details: dict | None = None,
) -> None:
    """Add an audit entry to the session; it is committed with the caller's transaction."""
    db.add(
        AuditLog(
            organization_id=organization_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details,
        )
    )
