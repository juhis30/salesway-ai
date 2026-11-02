"""Analytics queries. All results are computed in SQL from approved metric definitions."""

import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.analytics import metrics
from app.analytics.periods import month_end, month_start, previous_period
from app.models import Customer, Product, SalesOrder, SalesOrderItem, SalesTarget, User

ZERO = Decimal("0")


@dataclass(frozen=True)
class Filters:
    organization_id: uuid.UUID
    start: date
    end: date
    # None = every salesperson in the organisation (already intersected with the caller's scope).
    salesperson_ids: frozenset[uuid.UUID] | None = None
    territory_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    customer_segment: str | None = None
    customer_id: uuid.UUID | None = None

    @property
    def targets_applicable(self) -> bool:
        # Targets are set per salesperson per month; they don't split by product, customer or territory.
        return not (self.product_id or self.customer_segment or self.territory_id or self.customer_id)


def _dec(value) -> Decimal:
    return ZERO if value is None else Decimal(str(value))


def _lines(stmt: Select, f: Filters) -> Select:
    """Restrict a query over SalesOrderItem JOIN SalesOrder to the filters and revenue rules."""
    stmt = stmt.select_from(SalesOrderItem).join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id).where(
        SalesOrder.organization_id == f.organization_id,
        SalesOrder.status.in_(metrics.REVENUE_STATUSES),
        SalesOrder.order_date.between(f.start, f.end),
    )
    if f.salesperson_ids is not None:
        stmt = stmt.where(SalesOrder.salesperson_id.in_(f.salesperson_ids))
    if f.territory_id is not None:
        stmt = stmt.where(SalesOrder.territory_id == f.territory_id)
    if f.product_id is not None:
        stmt = stmt.where(SalesOrderItem.product_id == f.product_id)
    if f.customer_id is not None:
        stmt = stmt.where(SalesOrder.customer_id == f.customer_id)
    if f.customer_segment is not None:
        stmt = stmt.where(
            SalesOrder.customer_id.in_(
                select(Customer.id).where(
                    Customer.organization_id == f.organization_id, Customer.segment == f.customer_segment
                )
            )
        )
    return stmt


def revenue_totals(db: Session, f: Filters) -> tuple[Decimal, int, int]:
    """(revenue, order count, distinct buying customers) for the filtered period."""
    row = db.execute(
        _lines(
            select(
                func.sum(SalesOrderItem.line_amount),
                func.count(distinct(SalesOrder.id)),
                func.count(distinct(SalesOrder.customer_id)),
            ),
            f,
        )
    ).one()
    return _dec(row[0]), row[1], row[2]


def target_total(db: Session, f: Filters) -> Decimal | None:
    """Sum of monthly targets for every month that overlaps the period (full-month targets)."""
    if not f.targets_applicable:
        return None
    stmt = select(func.sum(SalesTarget.target_amount)).where(
        SalesTarget.organization_id == f.organization_id,
        SalesTarget.period.between(month_start(f.start), f.end),
    )
    if f.salesperson_ids is not None:
        stmt = stmt.where(SalesTarget.user_id.in_(f.salesperson_ids))
    value = db.scalar(stmt)
    return None if value is None else _dec(value)


def data_as_of(db: Session, organization_id: uuid.UUID) -> date | None:
    """Most recent order date on record: tells the user how fresh the numbers are."""
    return db.scalar(select(func.max(SalesOrder.order_date)).where(SalesOrder.organization_id == organization_id))


def data_starts(db: Session, organization_id: uuid.UUID) -> date | None:
    return db.scalar(select(func.min(SalesOrder.order_date)).where(SalesOrder.organization_id == organization_id))


def overview(db: Session, f: Filters, today: date) -> dict:
    revenue, orders, customers = revenue_totals(db, f)
    prev_start, prev_end = previous_period(f.start, f.end)
    prev_revenue, prev_orders, _ = revenue_totals(db, replace(f, start=prev_start, end=prev_end))
    # Growth against a window that starts before the organisation's data begins would compare
    # against "no data" and produce meaningless figures like +900%.
    first = data_starts(db, f.organization_id)
    comparable = first is not None and month_start(first) <= prev_start

    target = target_total(db, f)
    gap = metrics.target_gap(target, revenue)
    # A daily run rate only makes sense for a single month that is still in progress (today counts).
    single_month = (f.start.year, f.start.month) == (f.end.year, f.end.month)
    days_remaining = (month_end(f.end) - today).days + 1 if single_month and f.start <= today <= f.end else 0

    return {
        "period": {"start": f.start, "end": f.end},
        "comparison_period": {"start": prev_start, "end": prev_end},
        "revenue": revenue,
        "orders": orders,
        "average_order_value": metrics.average_order_value(revenue, orders),
        "active_customers": customers,
        "target": target,
        "achievement_pct": metrics.achievement_pct(revenue, target),
        "target_gap": gap,
        "required_daily_run_rate": metrics.required_daily_run_rate(gap, days_remaining),
        "previous_revenue": prev_revenue,
        "previous_orders": prev_orders,
        "comparison_available": comparable,
        "revenue_growth_pct": metrics.growth_pct(revenue, prev_revenue) if comparable else None,
        "orders_growth_pct": metrics.growth_pct(Decimal(orders), Decimal(prev_orders)) if comparable else None,
        "data_as_of": data_as_of(db, f.organization_id),
    }


def trend(db: Session, f: Filters, granularity: str) -> list[dict]:
    """Revenue and orders per day or month. Aggregated by day in SQL (portable), bucketed here."""
    rows = db.execute(
        _lines(
            select(
                SalesOrder.order_date,
                func.sum(SalesOrderItem.line_amount),
                func.count(distinct(SalesOrder.id)),
            ),
            f,
        ).group_by(SalesOrder.order_date)
    ).all()

    buckets: dict[date, list] = defaultdict(lambda: [ZERO, 0])
    for day, revenue, orders in rows:
        key = month_start(day) if granularity == "month" else day
        buckets[key][0] += _dec(revenue)
        buckets[key][1] += orders  # an order has one date, so per-day distinct counts add up

    targets: dict[date, Decimal] = {}
    if granularity == "month" and f.targets_applicable:
        stmt = select(SalesTarget.period, func.sum(SalesTarget.target_amount)).where(
            SalesTarget.organization_id == f.organization_id,
            SalesTarget.period.between(month_start(f.start), f.end),
        )
        if f.salesperson_ids is not None:
            stmt = stmt.where(SalesTarget.user_id.in_(f.salesperson_ids))
        targets = {period: _dec(total) for period, total in db.execute(stmt.group_by(SalesTarget.period))}

    return [
        {
            "period": key,
            "revenue": buckets[key][0] if key in buckets else ZERO,
            "orders": buckets[key][1] if key in buckets else 0,
            "target": targets.get(key),
        }
        for key in sorted(set(buckets) | set(targets))
    ]


def leaderboard(db: Session, f: Filters) -> list[dict]:
    rows = db.execute(
        _lines(
            select(
                SalesOrder.salesperson_id,
                func.sum(SalesOrderItem.line_amount),
                func.count(distinct(SalesOrder.id)),
            ),
            f,
        ).group_by(SalesOrder.salesperson_id)
    ).all()
    sales = {sp: (_dec(rev), n) for sp, rev, n in rows}

    targets: dict[uuid.UUID, Decimal] = {}
    if f.targets_applicable:
        stmt = select(SalesTarget.user_id, func.sum(SalesTarget.target_amount)).where(
            SalesTarget.organization_id == f.organization_id,
            SalesTarget.period.between(month_start(f.start), f.end),
        )
        if f.salesperson_ids is not None:
            stmt = stmt.where(SalesTarget.user_id.in_(f.salesperson_ids))
        targets = {uid: _dec(t) for uid, t in db.execute(stmt.group_by(SalesTarget.user_id))}

    people_ids = [p for p in set(sales) | set(targets) if p is not None]
    names = (
        dict(db.execute(select(User.id, User.full_name).where(User.id.in_(people_ids))).tuples().all())
        if people_ids
        else {}
    )

    result = []
    for person in people_ids:
        revenue, orders = sales.get(person, (ZERO, 0))
        target = targets.get(person)
        result.append(
            {
                "salesperson_id": person,
                "name": names.get(person, "Unknown"),
                "revenue": revenue,
                "orders": orders,
                "average_order_value": metrics.average_order_value(revenue, orders),
                "target": target,
                "achievement_pct": metrics.achievement_pct(revenue, target),
            }
        )
    result.sort(key=lambda r: r["revenue"], reverse=True)
    for rank, row in enumerate(result, start=1):
        row["rank"] = rank
    return result


def top_customers(db: Session, f: Filters, limit: int) -> list[dict]:
    revenue = func.sum(SalesOrderItem.line_amount).label("revenue")
    rows = db.execute(
        _lines(
            select(Customer.id, Customer.name, Customer.segment, revenue, func.count(distinct(SalesOrder.id)), func.max(SalesOrder.order_date)),
            f,
        )
        .join(Customer, SalesOrder.customer_id == Customer.id)
        .group_by(Customer.id, Customer.name, Customer.segment)
        .order_by(revenue.desc())
        .limit(limit)
    ).all()
    total, _, _ = revenue_totals(db, f)
    return [
        {
            "customer_id": cid,
            "name": name,
            "segment": segment,
            "revenue": _dec(rev),
            "orders": orders,
            "last_order_date": last,
            "share_pct": round(float(_dec(rev) / total * 100), 1) if total else None,
        }
        for cid, name, segment, rev, orders, last in rows
    ]


def top_products(db: Session, f: Filters, limit: int) -> list[dict]:
    revenue = func.sum(SalesOrderItem.line_amount).label("revenue")
    rows = db.execute(
        _lines(
            select(
                Product.id,
                Product.sku,
                Product.name,
                Product.category,
                revenue,
                func.sum(SalesOrderItem.quantity),
                func.count(distinct(SalesOrder.id)),
                func.max(SalesOrder.order_date),
            ),
            f,
        )
        .join(Product, SalesOrderItem.product_id == Product.id)
        .group_by(Product.id, Product.sku, Product.name, Product.category)
        .order_by(revenue.desc())
        .limit(limit)
    ).all()
    total, _, _ = revenue_totals(db, f)
    return [
        {
            "product_id": pid,
            "sku": sku,
            "name": name,
            "category": category,
            "revenue": _dec(rev),
            "units": _dec(units),
            "orders": orders,
            "last_order_date": last,
            "average_unit_price": (_dec(rev) / _dec(units)).quantize(Decimal("0.01")) if units else None,
            "share_pct": round(float(_dec(rev) / total * 100), 1) if total else None,
        }
        for pid, sku, name, category, rev, units, orders, last in rows
    ]
