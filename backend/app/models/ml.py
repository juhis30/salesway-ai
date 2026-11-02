import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TenantMixin, TimestampMixin, utcnow
from app.models.sales import Money


class MLModel(IdMixin, TenantMixin, Base):
    """One training run of one kind of model, with how it scored on held-out history.

    status: "active" (in use), "rejected" (didn't beat the simple baseline, so not used),
    "insufficient_data", or "retired" (replaced by a newer version).
    """

    __tablename__ = "ml_models"
    __table_args__ = (Index("ix_ml_models_org_kind", "organization_id", "kind"),)

    kind: Mapped[str] = mapped_column(String(32))  # month_end_forecast | next_month_forecast | churn | deal_win
    version: Mapped[int] = mapped_column(Integer)
    algorithm: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data_start: Mapped[date | None] = mapped_column(Date)
    data_end: Mapped[date | None] = mapped_column(Date)
    training_rows: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    # Everything needed to reproduce or explain it: chosen method, error samples, coefficients...
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)


class Prediction(IdMixin, TenantMixin, Base):
    __tablename__ = "predictions"
    __table_args__ = (Index("ix_predictions_lookup", "organization_id", "kind", "entity_type", "entity_id"),)

    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ml_models.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    entity_type: Mapped[str] = mapped_column(String(32))  # organization | salesperson | customer | opportunity
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    as_of: Mapped[date] = mapped_column(Date)
    target_period: Mapped[date | None] = mapped_column(Date)  # e.g. the month being forecast
    score: Mapped[float | None] = mapped_column(Float)        # a probability, 0-1
    value: Mapped[Decimal | None] = mapped_column(Money)      # a forecast amount
    lower: Mapped[Decimal | None] = mapped_column(Money)
    upper: Mapped[Decimal | None] = mapped_column(Money)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recommendation(IdMixin, TimestampMixin, TenantMixin, Base):
    """A suggested next action for one salesperson, with the data that justifies it."""

    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_user_status", "organization_id", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # win_back | follow_up | revive_deal | cross_sell
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text)
    expected_value: Mapped[Decimal | None] = mapped_column(Money)  # rupees at stake / to gain
    score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | done | dismissed
    as_of: Mapped[date] = mapped_column(Date)
    details: Mapped[dict | None] = mapped_column(JSON)
