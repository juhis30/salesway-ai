import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analytics import insights, service
from app.analytics.periods import org_today
from app.api.v1.analytics import Period, get_filters
from app.core.database import get_db
from app.core.deps import get_current_org, get_current_user
from app.models import ActivityStatus, ActivityType, OpportunityStage, Organization, SalesActivity, User
from app.services import audit
from app.services.access import salesperson_scope

router = APIRouter(tags=["insights"])

# --- Customer health --------------------------------------------------------------------------

HealthStatus = Literal["active", "at_risk", "dormant"]


class HealthSummary(BaseModel):
    active: int
    at_risk: int
    dormant: int
    never_ordered: int
    new_in_period: int
    returning_in_period: int
    repeat_rate_pct: float | None
    monthly_value_at_risk: Decimal


class HealthRow(BaseModel):
    customer_id: uuid.UUID
    name: str
    code: str | None
    segment: str | None
    owner_name: str | None
    status: HealthStatus
    reason: str
    first_order_date: date
    last_order_date: date
    days_since_last_order: int
    typical_gap_days: int | None
    orders: int
    monthly_value: Decimal


class HealthOut(BaseModel):
    as_of: date
    summary: HealthSummary
    rules: dict[str, float]
    customers: list[HealthRow]
    total: int


@router.get("/analytics/customer-health", response_model=HealthOut)
def customer_health(
    status_filter: HealthStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
    f: service.Filters = Depends(get_filters),
    db: Session = Depends(get_db),
):
    """Active / at-risk / dormant customers as of the period end, plus new vs returning buyers in the period."""
    return insights.customer_health(db, f, status_filter, limit)


# --- Product trends ---------------------------------------------------------------------------


class ProductTrendRow(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    category: str | None
    revenue: Decimal
    previous_revenue: Decimal
    growth_pct: float | None
    units: Decimal
    previous_units: Decimal
    buyers: int
    previous_buyers: int
    share_pct: float | None
    trend: Literal["rising", "steady", "declining", "new", "stopped"] | None


class ProductTrendsOut(BaseModel):
    period: Period
    comparison_period: Period
    comparison_available: bool
    trends_available: bool
    min_trend_days: int
    threshold_pct: float
    counts: dict[str, int]
    products: list[ProductTrendRow]


@router.get("/analytics/product-trends", response_model=ProductTrendsOut)
def product_trends(f: service.Filters = Depends(get_filters), db: Session = Depends(get_db)):
    return insights.product_trends(db, f)


# --- Pipeline ---------------------------------------------------------------------------------


class StageRow(BaseModel):
    stage: OpportunityStage
    count: int
    value: Decimal
    weight_pct: int
    weighted_value: Decimal


class DealRow(BaseModel):
    opportunity_id: uuid.UUID
    code: str
    name: str
    customer_id: uuid.UUID
    customer_name: str | None
    owner_name: str | None
    stage: OpportunityStage
    amount: Decimal
    opened_on: date
    expected_close: date | None
    last_activity_date: date | None
    days_open: int
    days_idle: int
    stalled: bool
    overdue: bool


class PipelineOut(BaseModel):
    as_of: date
    period: Period
    open_count: int
    open_value: Decimal
    weighted_value: Decimal
    stalled_count: int
    overdue_count: int
    by_stage: list[StageRow]
    won_count: int
    won_value: Decimal
    lost_count: int
    win_rate_pct: float | None
    average_won_deal: Decimal | None
    average_cycle_days: int | None
    rules: dict[str, int]
    deals: list[DealRow]


@router.get("/analytics/pipeline", response_model=PipelineOut)
def pipeline(
    f: service.Filters = Depends(get_filters),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    """Open deals as of today; won/lost and win rate for deals closed in the period."""
    return insights.pipeline(db, f, org_today(org.timezone))


# --- Team activity ----------------------------------------------------------------------------


class ActivityRow(BaseModel):
    salesperson_id: uuid.UUID
    name: str
    visits: int
    calls: int
    meetings: int
    whatsapp: int
    total_activities: int
    customers_contacted: int
    follow_ups_overdue: int
    follow_ups_upcoming: int
    orders: int
    revenue: Decimal
    visit_to_order_pct: float | None
    revenue_per_visit: Decimal | None


class TeamActivityOut(BaseModel):
    period: Period
    rules: dict[str, int]
    totals: dict[str, int]
    people: list[ActivityRow]


@router.get("/analytics/team-activity", response_model=TeamActivityOut)
def team_activity(
    f: service.Filters = Depends(get_filters),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    return insights.team_activity(db, f, org_today(org.timezone))


class FollowUpOut(BaseModel):
    id: uuid.UUID
    activity_type: ActivityType
    due_date: date
    days_overdue: int
    customer_id: uuid.UUID | None
    customer_name: str | None
    opportunity_name: str | None
    salesperson_name: str | None
    notes: str | None


@router.get("/activities/follow-ups", response_model=list[FollowUpOut])
def follow_ups(
    when: Literal["overdue", "upcoming"] = "overdue",
    limit: int = Query(default=100, ge=1, le=500),
    f: service.Filters = Depends(get_filters),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    return insights.follow_ups(db, f, org_today(org.timezone), when, limit)


class ActivityUpdate(BaseModel):
    status: ActivityStatus


@router.patch("/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_activity(
    activity_id: uuid.UUID,
    body: ActivityUpdate,
    user: User = Depends(get_current_user),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    """Mark a follow-up done (or re-open it). Allowed for its owner and anyone whose scope includes them."""
    activity = db.get(SalesActivity, activity_id)
    if activity is None or activity.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Activity not found")
    scope = salesperson_scope(db, user)
    if scope is not None and activity.user_id not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot change this activity")
    if body.status == ActivityStatus.DONE and activity.status == ActivityStatus.PLANNED:
        # Completing a follow-up records it as done today, so it counts in today's activity.
        activity.activity_date = org_today(org.timezone)
    activity.status = body.status
    audit.record(db, user.organization_id, user.id, "activity.updated", "activity", activity.id, {"status": body.status.value})
    db.commit()
