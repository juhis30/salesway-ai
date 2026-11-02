import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analytics import service
from app.analytics.periods import month_start, org_today
from app.core.database import get_db
from app.core.deps import get_current_org, get_current_user
from app.models import Organization, User
from app.services.access import resolve_salesperson_filter, visible_customer

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_RANGE = timedelta(days=366 * 3)


def get_filters(
    start: date | None = Query(default=None, description="Defaults to the 1st of the current month"),
    end: date | None = Query(default=None, description="Defaults to today (organisation timezone)"),
    salesperson_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    territory_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    customer_segment: str | None = Query(default=None, max_length=64),
    customer_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> service.Filters:
    today = org_today(org.timezone)
    end = end or today
    start = start or month_start(end)
    if start > end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start must be on or before end")
    if end - start > MAX_RANGE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Date range cannot exceed 3 years")

    scope = resolve_salesperson_filter(db, user, salesperson_id, team_id)
    if customer_id is not None:
        visible_customer(db, user, customer_id)
    return service.Filters(
        organization_id=user.organization_id,
        start=start,
        end=end,
        salesperson_ids=None if scope is None else frozenset(scope),
        territory_id=territory_id,
        product_id=product_id,
        customer_segment=customer_segment,
        customer_id=customer_id,
    )


class Period(BaseModel):
    start: date
    end: date


class OverviewOut(BaseModel):
    period: Period
    comparison_period: Period
    revenue: Decimal
    orders: int
    average_order_value: Decimal | None
    active_customers: int
    target: Decimal | None
    achievement_pct: float | None
    target_gap: Decimal | None
    required_daily_run_rate: Decimal | None
    previous_revenue: Decimal
    previous_orders: int
    comparison_available: bool
    revenue_growth_pct: float | None
    orders_growth_pct: float | None
    data_as_of: date | None


class TrendPoint(BaseModel):
    period: date
    revenue: Decimal
    orders: int
    target: Decimal | None


class LeaderboardRow(BaseModel):
    rank: int
    salesperson_id: uuid.UUID
    name: str
    revenue: Decimal
    orders: int
    average_order_value: Decimal | None
    target: Decimal | None
    achievement_pct: float | None


class CustomerRow(BaseModel):
    customer_id: uuid.UUID
    name: str
    segment: str | None
    revenue: Decimal
    orders: int
    last_order_date: date
    share_pct: float | None


class ProductRow(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    category: str | None
    revenue: Decimal
    units: Decimal
    orders: int
    last_order_date: date
    average_unit_price: Decimal | None
    share_pct: float | None


@router.get("/overview", response_model=OverviewOut)
def overview(
    f: service.Filters = Depends(get_filters),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    """Executive KPIs: revenue, orders, AOV, target achievement and growth."""
    return service.overview(db, f, org_today(org.timezone))


@router.get("/trend", response_model=list[TrendPoint])
def trend(
    granularity: Literal["day", "month"] = "month",
    f: service.Filters = Depends(get_filters),
    db: Session = Depends(get_db),
):
    return service.trend(db, f, granularity)


@router.get("/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(f: service.Filters = Depends(get_filters), db: Session = Depends(get_db)):
    return service.leaderboard(db, f)


@router.get("/top-customers", response_model=list[CustomerRow])
def top_customers(
    limit: int = Query(default=10, ge=1, le=100),
    f: service.Filters = Depends(get_filters),
    db: Session = Depends(get_db),
):
    return service.top_customers(db, f, limit)


@router.get("/top-products", response_model=list[ProductRow])
def top_products(
    limit: int = Query(default=10, ge=1, le=100),
    f: service.Filters = Depends(get_filters),
    db: Session = Depends(get_db),
):
    return service.top_products(db, f, limit)
