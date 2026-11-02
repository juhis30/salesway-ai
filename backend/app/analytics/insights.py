"""Customer health, product trends, pipeline and team activity.

These are rule-based and explainable on purpose: every status says which rule produced it.
Predictive models (Phase 4) will add probabilities alongside, not replace these.
"""

import statistics
import uuid
from bisect import bisect_left
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.analytics import metrics
from app.analytics.periods import month_start, previous_period
from app.analytics.service import ZERO, Filters, _dec, _lines, data_starts
from app.models import (
    OPEN_STAGES,
    ActivityStatus,
    ActivityType,
    Customer,
    Opportunity,
    OpportunityStage,
    Product,
    SalesActivity,
    SalesOrder,
    SalesOrderItem,
    User,
)

# --- Rules (documented in docs/metrics.md) ----------------------------------------------------

DORMANT_DAYS = 60              # no order for this long -> dormant
AT_RISK_GAP_MULTIPLIER = 1.5   # overdue vs the customer's own typical gap -> at risk
AT_RISK_MIN_DAYS = 14          # ...but never flag before two weeks
DEFAULT_GAP_DAYS = 30          # typical gap when a customer has too few orders to measure
TREND_THRESHOLD_PCT = 15.0     # product growth beyond +/- this is rising / declining
MIN_TREND_DAYS = 14            # shorter periods are too noisy to call a trend
STALLED_DAYS = 21              # open deal with no activity for this long -> stalled
VISIT_TO_ORDER_DAYS = 7        # an order within this many days after a visit counts as converted

# Fixed stage weights for "weighted pipeline". They are conventions, not predictions.
STAGE_WEIGHTS = {
    OpportunityStage.PROSPECTING: Decimal("0.10"),
    OpportunityStage.QUALIFICATION: Decimal("0.25"),
    OpportunityStage.PROPOSAL: Decimal("0.50"),
    OpportunityStage.NEGOTIATION: Decimal("0.75"),
}

ALL_TIME = date(1900, 1, 1)


def _visible_customers(f: Filters) -> Select:
    """Customer ids in scope: assigned to someone the caller can see, plus territory/segment filters."""
    stmt = select(Customer.id).where(Customer.organization_id == f.organization_id)
    if f.salesperson_ids is not None:
        stmt = stmt.where(Customer.owner_id.in_(f.salesperson_ids))
    if f.territory_id is not None:
        stmt = stmt.where(Customer.territory_id == f.territory_id)
    if f.customer_segment is not None:
        stmt = stmt.where(Customer.segment == f.customer_segment)
    if f.customer_id is not None:
        stmt = stmt.where(Customer.id == f.customer_id)
    return stmt


# --- Customer health --------------------------------------------------------------------------


def customer_health(db: Session, f: Filters, status_filter: str | None = None, limit: int = 200) -> dict:
    """Classify each customer as of the period end, using their whole order history up to then."""
    as_of = f.end
    history = replace(f, start=ALL_TIME, end=as_of, product_id=None)
    rows = db.execute(
        _lines(
            select(SalesOrder.customer_id, SalesOrder.order_date, func.sum(SalesOrderItem.line_amount)),
            history,
        )
        .where(SalesOrder.customer_id.in_(_visible_customers(f)))
        .group_by(SalesOrder.customer_id, SalesOrder.id, SalesOrder.order_date)
    ).all()

    orders: dict[uuid.UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    for customer_id, order_date, revenue in rows:
        orders[customer_id].append((order_date, _dec(revenue)))

    gaps: dict[uuid.UUID, float | None] = {}
    for customer_id, items in orders.items():
        days = sorted({d for d, _ in items})
        intervals = [(b - a).days for a, b in zip(days, days[1:])]
        gaps[customer_id] = statistics.mean(intervals) if len(intervals) >= 2 else None
    measured = [g for g in gaps.values() if g is not None]
    fallback_gap = statistics.median(measured) if measured else DEFAULT_GAP_DAYS

    customers = {
        c.id: c
        for c in db.scalars(select(Customer).where(Customer.id.in_(_visible_customers(f))))
    }
    owner_ids = {c.owner_id for c in customers.values() if c.owner_id}
    owners = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(owner_ids))).tuples().all()) if owner_ids else {}

    summary = {"active": 0, "at_risk": 0, "dormant": 0, "never_ordered": 0, "new_in_period": 0, "returning_in_period": 0}
    value_at_risk = ZERO
    result = []
    for customer_id, customer in customers.items():
        items = orders.get(customer_id)
        if not items:
            summary["never_ordered"] += 1
            continue
        first = min(d for d, _ in items)
        last = max(d for d, _ in items)
        days_since = (as_of - last).days
        gap = gaps[customer_id]
        expected_gap = gap if gap is not None else fallback_gap

        if days_since >= DORMANT_DAYS:
            status = "dormant"
            reason = f"No order for {days_since} days ({DORMANT_DAYS}+ days is dormant)"
        elif days_since > max(expected_gap * AT_RISK_GAP_MULTIPLIER, AT_RISK_MIN_DAYS):
            status = "at_risk"
            reason = f"No order for {days_since} days; usually orders every {round(expected_gap)} days"
        else:
            status = "active"
            when = "today" if days_since == 0 else "yesterday" if days_since == 1 else f"{days_since} days ago"
            reason = f"Ordered {when}; usually every {round(expected_gap)} days"
        summary[status] += 1

        if first >= f.start:
            summary["new_in_period"] += 1
        elif any(f.start <= d <= f.end for d, _ in items):
            summary["returning_in_period"] += 1

        # Typical monthly revenue over the 6 months before their last order: what is at stake.
        window_start = last - timedelta(days=182)
        monthly_value = (sum((r for d, r in items if d > window_start), ZERO) / 6).quantize(Decimal("0.01"))
        if status != "active":
            value_at_risk += monthly_value

        if status_filter and status != status_filter:
            continue
        result.append(
            {
                "customer_id": customer_id,
                "name": customer.name,
                "code": customer.code,
                "segment": customer.segment,
                "owner_name": owners.get(customer.owner_id),
                "status": status,
                "reason": reason,
                "first_order_date": first,
                "last_order_date": last,
                "days_since_last_order": days_since,
                "typical_gap_days": round(gap) if gap is not None else None,
                "orders": len(items),
                "monthly_value": monthly_value,
            }
        )

    order = {"dormant": 0, "at_risk": 1, "active": 2}
    result.sort(key=lambda r: (order[r["status"]], -r["monthly_value"]))
    ordered_in_period = summary["new_in_period"] + summary["returning_in_period"]
    return {
        "as_of": as_of,
        "summary": {
            **summary,
            "monthly_value_at_risk": value_at_risk,
            "repeat_rate_pct": round(summary["returning_in_period"] / ordered_in_period * 100, 1) if ordered_in_period else None,
        },
        "rules": {
            "dormant_days": DORMANT_DAYS,
            "at_risk_gap_multiplier": AT_RISK_GAP_MULTIPLIER,
            "at_risk_min_days": AT_RISK_MIN_DAYS,
        },
        "customers": result[:limit],
        "total": len(result),
    }


# --- Product trends -----------------------------------------------------------------------


def _product_period(db: Session, f: Filters) -> dict[uuid.UUID, tuple[Decimal, Decimal, int]]:
    rows = db.execute(
        _lines(
            select(
                SalesOrderItem.product_id,
                func.sum(SalesOrderItem.line_amount),
                func.sum(SalesOrderItem.quantity),
                func.count(distinct(SalesOrder.customer_id)),
            ),
            f,
        ).group_by(SalesOrderItem.product_id)
    ).all()
    return {pid: (_dec(rev), _dec(units), buyers) for pid, rev, units, buyers in rows}


def product_trends(db: Session, f: Filters) -> dict:
    prev_start, prev_end = previous_period(f.start, f.end)
    first = data_starts(db, f.organization_id)
    comparable = first is not None and month_start(first) <= prev_start
    long_enough = (f.end - f.start).days + 1 >= MIN_TREND_DAYS
    current = _product_period(db, f)
    previous = _product_period(db, replace(f, start=prev_start, end=prev_end)) if comparable else {}

    ids = set(current) | set(previous)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}
    total = sum((r for r, _, _ in current.values()), ZERO)

    rows = []
    for pid in ids:
        rev, units, buyers = current.get(pid, (ZERO, ZERO, 0))
        prev_rev, prev_units, prev_buyers = previous.get(pid, (ZERO, ZERO, 0))
        growth = metrics.growth_pct(rev, prev_rev) if comparable else None
        if not comparable or not long_enough:
            trend = None
        elif prev_rev == 0:
            trend = "new" if rev > 0 else None
        elif rev == 0:
            trend = "stopped"
        elif growth >= TREND_THRESHOLD_PCT:
            trend = "rising"
        elif growth <= -TREND_THRESHOLD_PCT:
            trend = "declining"
        else:
            trend = "steady"
        p = products[pid]
        rows.append(
            {
                "product_id": pid,
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "revenue": rev,
                "previous_revenue": prev_rev,
                "growth_pct": growth,
                "units": units,
                "previous_units": prev_units,
                "buyers": buyers,
                "previous_buyers": prev_buyers,
                "share_pct": round(float(rev / total * 100), 1) if total else None,
                "trend": trend,
            }
        )
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    counts = defaultdict(int)
    for r in rows:
        if r["trend"]:
            counts[r["trend"]] += 1
    return {
        "period": {"start": f.start, "end": f.end},
        "comparison_period": {"start": prev_start, "end": prev_end},
        "comparison_available": comparable,
        "trends_available": comparable and long_enough,
        "min_trend_days": MIN_TREND_DAYS,
        "threshold_pct": TREND_THRESHOLD_PCT,
        "counts": dict(counts),
        "products": rows,
    }


# --- Pipeline -----------------------------------------------------------------------------


def _opportunities(f: Filters) -> Select:
    stmt = select(Opportunity).where(Opportunity.organization_id == f.organization_id)
    if f.salesperson_ids is not None:
        stmt = stmt.where(Opportunity.owner_id.in_(f.salesperson_ids))
    if f.territory_id or f.customer_segment or f.customer_id:
        stmt = stmt.where(Opportunity.customer_id.in_(_visible_customers(replace(f, salesperson_ids=None))))
    return stmt


def pipeline(db: Session, f: Filters, today: date) -> dict:
    """Open deals are a snapshot as of today; won/lost figures are for deals closed in the period."""
    deals = db.scalars(_opportunities(f)).all()
    open_deals = [d for d in deals if d.stage in OPEN_STAGES]
    closed = [d for d in deals if d.closed_on and f.start <= d.closed_on <= f.end]
    won = [d for d in closed if d.stage == OpportunityStage.WON]
    lost = [d for d in closed if d.stage == OpportunityStage.LOST]

    last_activity = dict(
        db.execute(
            select(SalesActivity.opportunity_id, func.max(SalesActivity.activity_date))
            .where(
                SalesActivity.organization_id == f.organization_id,
                SalesActivity.status == ActivityStatus.DONE,
                SalesActivity.opportunity_id.in_([d.id for d in open_deals]),
            )
            .group_by(SalesActivity.opportunity_id)
        ).tuples().all()
    ) if open_deals else {}

    names = {}
    ids = {d.customer_id for d in open_deals}
    if ids:
        names = dict(db.execute(select(Customer.id, Customer.name).where(Customer.id.in_(ids))).tuples().all())
    owner_ids = {d.owner_id for d in open_deals if d.owner_id}
    owners = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(owner_ids))).tuples().all()) if owner_ids else {}

    by_stage = []
    for stage in OPEN_STAGES:
        in_stage = [d for d in open_deals if d.stage == stage]
        value = sum((d.amount for d in in_stage), ZERO)
        by_stage.append(
            {
                "stage": stage,
                "count": len(in_stage),
                "value": value,
                "weight_pct": int(STAGE_WEIGHTS[stage] * 100),
                "weighted_value": (value * STAGE_WEIGHTS[stage]).quantize(Decimal("0.01")),
            }
        )

    rows = []
    for d in open_deals:
        last = last_activity.get(d.id)
        idle_since = last or d.opened_on
        stalled = (today - idle_since).days > STALLED_DAYS
        overdue = d.expected_close is not None and d.expected_close < today
        rows.append(
            {
                "opportunity_id": d.id,
                "code": d.code,
                "name": d.name,
                "customer_id": d.customer_id,
                "customer_name": names.get(d.customer_id),
                "owner_name": owners.get(d.owner_id),
                "stage": d.stage,
                "amount": d.amount,
                "opened_on": d.opened_on,
                "expected_close": d.expected_close,
                "last_activity_date": last,
                "days_open": (today - d.opened_on).days,
                "days_idle": (today - idle_since).days,
                "stalled": stalled,
                "overdue": overdue,
            }
        )
    rows.sort(key=lambda r: r["amount"], reverse=True)

    won_value = sum((d.amount for d in won), ZERO)
    cycle_days = [(d.closed_on - d.opened_on).days for d in won]
    return {
        "as_of": today,
        "period": {"start": f.start, "end": f.end},
        "open_count": len(open_deals),
        "open_value": sum((d.amount for d in open_deals), ZERO),
        "weighted_value": sum((s["weighted_value"] for s in by_stage), ZERO),
        "stalled_count": sum(r["stalled"] for r in rows),
        "overdue_count": sum(r["overdue"] for r in rows),
        "by_stage": by_stage,
        "won_count": len(won),
        "won_value": won_value,
        "lost_count": len(lost),
        "win_rate_pct": round(len(won) / len(closed) * 100, 1) if closed else None,
        "average_won_deal": (won_value / len(won)).quantize(Decimal("0.01")) if won else None,
        "average_cycle_days": round(statistics.mean(cycle_days)) if cycle_days else None,
        "rules": {"stalled_days": STALLED_DAYS},
        "deals": rows,
    }


# --- Team activity ------------------------------------------------------------------------


def team_activity(db: Session, f: Filters, today: date) -> dict:
    """Per salesperson: activities done in the period, follow-ups, orders and visit conversion."""
    people_stmt = select(User.id, User.full_name).where(
        User.organization_id == f.organization_id, User.deleted_at.is_(None)
    )
    if f.salesperson_ids is not None:
        people_stmt = people_stmt.where(User.id.in_(f.salesperson_ids))

    act_filter = [SalesActivity.organization_id == f.organization_id]
    if f.salesperson_ids is not None:
        act_filter.append(SalesActivity.user_id.in_(f.salesperson_ids))
    if f.territory_id or f.customer_segment or f.customer_id:
        act_filter.append(SalesActivity.customer_id.in_(_visible_customers(replace(f, salesperson_ids=None))))

    done = db.execute(
        select(SalesActivity.user_id, SalesActivity.activity_type, SalesActivity.customer_id, SalesActivity.activity_date)
        .where(*act_filter, SalesActivity.status == ActivityStatus.DONE, SalesActivity.activity_date.between(f.start, f.end))
    ).all()
    planned = db.execute(
        select(SalesActivity.user_id, SalesActivity.activity_date).where(*act_filter, SalesActivity.status == ActivityStatus.PLANNED)
    ).all()

    # Orders per customer in the period (+ conversion window) to test whether visits led to orders.
    order_rows = db.execute(
        _lines(
            select(SalesOrder.salesperson_id, SalesOrder.customer_id, SalesOrder.order_date, func.sum(SalesOrderItem.line_amount)),
            replace(f, end=f.end + timedelta(days=VISIT_TO_ORDER_DAYS), product_id=None),
        ).group_by(SalesOrder.id, SalesOrder.salesperson_id, SalesOrder.customer_id, SalesOrder.order_date)
    ).all()
    order_dates: dict[uuid.UUID, list[date]] = defaultdict(list)
    sales: dict[uuid.UUID, list] = defaultdict(lambda: [ZERO, 0])
    for salesperson, customer, order_date, revenue in order_rows:
        order_dates[customer].append(order_date)
        if order_date <= f.end and salesperson:
            sales[salesperson][0] += _dec(revenue)
            sales[salesperson][1] += 1
    for dates in order_dates.values():
        dates.sort()

    def converted(customer: uuid.UUID | None, visit_day: date) -> bool:
        dates = order_dates.get(customer, [])
        i = bisect_left(dates, visit_day)
        return i < len(dates) and dates[i] <= visit_day + timedelta(days=VISIT_TO_ORDER_DAYS)

    stats: dict[uuid.UUID, dict] = defaultdict(lambda: {t.value: 0 for t in ActivityType} | {"customers": set(), "converted": 0})
    for user_id, kind, customer, day in done:
        s = stats[user_id]
        s[kind.value] += 1
        if customer:
            s["customers"].add(customer)
        if kind == ActivityType.VISIT and converted(customer, day):
            s["converted"] += 1
    follow_ups: dict[uuid.UUID, list[int]] = defaultdict(lambda: [0, 0])  # overdue, upcoming
    for user_id, day in planned:
        follow_ups[user_id][0 if day < today else 1] += 1

    rows = []
    for user_id, name in db.execute(people_stmt).all():
        s = stats.get(user_id)
        revenue, orders = sales.get(user_id, (ZERO, 0))
        if s is None and not orders and user_id not in follow_ups:
            continue  # admins, analysts and others with no field activity
        s = s or stats[user_id]
        total = sum(s[t.value] for t in ActivityType)
        rows.append(
            {
                "salesperson_id": user_id,
                "name": name,
                "visits": s["visit"],
                "calls": s["call"],
                "meetings": s["meeting"],
                "whatsapp": s["whatsapp"],
                "total_activities": total,
                "customers_contacted": len(s["customers"]),
                "follow_ups_overdue": follow_ups[user_id][0],
                "follow_ups_upcoming": follow_ups[user_id][1],
                "orders": orders,
                "revenue": revenue,
                "visit_to_order_pct": round(s["converted"] / s["visit"] * 100, 1) if s["visit"] else None,
                "revenue_per_visit": (revenue / s["visit"]).quantize(Decimal("0.01")) if s["visit"] else None,
            }
        )
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return {
        "period": {"start": f.start, "end": f.end},
        "rules": {"visit_to_order_days": VISIT_TO_ORDER_DAYS},
        "totals": {
            k: sum(r[k] for r in rows)
            for k in ("visits", "calls", "meetings", "whatsapp", "total_activities", "follow_ups_overdue", "orders")
        },
        "people": rows,
    }


def follow_ups(db: Session, f: Filters, today: date, when: str, limit: int = 100) -> list[dict]:
    """Planned activities: 'overdue' (date before today) or 'upcoming'."""
    stmt = select(SalesActivity).where(
        SalesActivity.organization_id == f.organization_id, SalesActivity.status == ActivityStatus.PLANNED
    )
    if f.salesperson_ids is not None:
        stmt = stmt.where(SalesActivity.user_id.in_(f.salesperson_ids))
    if f.customer_id is not None:
        stmt = stmt.where(SalesActivity.customer_id == f.customer_id)
    if when == "overdue":
        stmt = stmt.where(SalesActivity.activity_date < today).order_by(SalesActivity.activity_date)
    else:
        stmt = stmt.where(SalesActivity.activity_date >= today).order_by(SalesActivity.activity_date)
    items = db.scalars(stmt.limit(limit)).all()

    customer_ids = {a.customer_id for a in items if a.customer_id}
    user_ids = {a.user_id for a in items}
    opp_ids = {a.opportunity_id for a in items if a.opportunity_id}
    customers = dict(db.execute(select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))).tuples().all()) if customer_ids else {}
    users = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids))).tuples().all()) if user_ids else {}
    opps = dict(db.execute(select(Opportunity.id, Opportunity.name).where(Opportunity.id.in_(opp_ids))).tuples().all()) if opp_ids else {}
    return [
        {
            "id": a.id,
            "activity_type": a.activity_type,
            "due_date": a.activity_date,
            "days_overdue": max((today - a.activity_date).days, 0),
            "customer_id": a.customer_id,
            "customer_name": customers.get(a.customer_id),
            "opportunity_name": opps.get(a.opportunity_id),
            "salesperson_id": a.user_id,
            "salesperson_name": users.get(a.user_id),
            "notes": a.notes,
        }
        for a in items
    ]
