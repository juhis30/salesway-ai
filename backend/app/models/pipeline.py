import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TenantMixin, TimestampMixin, str_enum
from app.models.enums import ActivityStatus, ActivityType, OpportunityStage
from app.models.sales import Money


class Opportunity(IdMixin, TimestampMixin, TenantMixin, Base):
    """A deal being worked. Prospects are stored as customers with no orders yet."""

    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)

    code: Mapped[str] = mapped_column(String(64))  # the source system's deal ID
    name: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    stage: Mapped[OpportunityStage] = mapped_column(str_enum(OpportunityStage), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    opened_on: Mapped[date] = mapped_column(Date)
    expected_close: Mapped[date | None] = mapped_column(Date)
    closed_on: Mapped[date | None] = mapped_column(Date, index=True)
    source: Mapped[str | None] = mapped_column(String(64))
    lost_reason: Mapped[str | None] = mapped_column(String(200))


class SalesActivity(IdMixin, TimestampMixin, TenantMixin, Base):
    """A visit/call/meeting that happened (done) or a follow-up that is planned."""

    __tablename__ = "sales_activities"
    __table_args__ = (
        Index("ix_sales_activities_org_date", "organization_id", "activity_date"),
        UniqueConstraint("organization_id", "external_id", name="uq_sales_activities_org_external_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    activity_type: Mapped[ActivityType] = mapped_column(str_enum(ActivityType))
    status: Mapped[ActivityStatus] = mapped_column(str_enum(ActivityStatus), default=ActivityStatus.DONE)
    # The date it happened, or for a planned follow-up, the date it is due.
    activity_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    # The source system's id (CRM task/call id), so a synced activity is updated, not duplicated.
    external_id: Mapped[str | None] = mapped_column(String(128))
