import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TenantMixin, TimestampMixin, str_enum
from app.models.enums import ImportEntity, ImportStatus


class ImportJob(IdMixin, TimestampMixin, TenantMixin, Base):
    """One uploaded file: previewed first, then confirmed (re-validated and written)."""

    __tablename__ = "import_jobs"

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    entity: Mapped[ImportEntity] = mapped_column(str_enum(ImportEntity))
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[ImportStatus] = mapped_column(str_enum(ImportStatus))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    column_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    # Raw rows are kept until confirmation so the import is re-validated against current data.
    raw_rows: Mapped[list | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
