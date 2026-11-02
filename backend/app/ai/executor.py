"""Run an Interpretation against the analytics engine and collect Facts.

Nothing here invents a number: every figure comes from app.analytics (the same code the dashboard
uses), and every query is limited to what the asking user may see.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.ai.catalog import Catalog, Entity, find_by_name
from app.ai.periods import resolve
from app.ai.schema import Facts, Intent, Interpretation, PeriodSpec
from app.analytics import insights, metrics, service
from app.analytics.periods import month_end, month_start, previous_period
from app.models import ORG_WIDE_ROLES, Organization, Role, User
from app.services.access import resolve_salesperson_filter


class NeedsClarification(Exception):
    def __init__(self, facts: Facts):
        self.facts = facts


@dataclass
class Resolved:
    salesperson: Entity | None = None
    customer: Entity | None = None
    product: Entity | None = None
    territory: Entity | None = None


ENTITY_LABEL = {"customer": "customer", "product": "product", "salesperson": "salesperson", "territory": "territory"}


def _resolve_entities(interp: Interpretation, catalog: Catalog) -> Resolved:
    out = Resolved()
    for kind, pool in (("customer", catalog.customers), ("product", catalog.products),
                       ("salesperson", catalog.people), ("territory", catalog.territories)):
        name = getattr(interp, kind)
        if not name:
            continue
        options = [n.strip() for n in name.split("|")]
        match = find_by_name(options[0], pool) if len(options) == 1 else None
        if len(options) > 1 or (match and match.ambiguous):
            candidates = [e.name for e in (match.entities if match else [])] or options
            raise NeedsClarification(
                Facts(
                    intent=interp.intent,
                    title=f"Which {ENTITY_LABEL[kind]} did you mean?",
                    scope="",
                    rows=[{"name": c} for c in candidates[:6]],
                    suggestions=[c for c in candidates[:4]],
                    clarify=True,
                )
            )
        if match is None:
            where = "among the customers you can see" if kind == "customer" else ""
            raise NeedsClarification(
                Facts(
                    intent=interp.intent,
                    title=f"I couldn't find a {ENTITY_LABEL[kind]} called “{name}” {where}".strip(),
                    scope="",
                    notes=["Check the spelling, or try the code / SKU."],
                    clarify=True,
                )
            )
        setattr(out, kind, match.entities[0])
    return out


class Executor:
    def __init__(self, db: Session, user: User, org: Organization, catalog: Catalog, today: date):
        self.db, self.user, self.org, self.catalog, self.today = db, user, org, catalog, today

    # --- helpers -------------------------------------------------------------------------------

    def filters(self, start: date, end: date, r: Resolved) -> service.Filters:
        # Raises HTTP 403/404 if the user asks about someone outside their scope.
        scope = resolve_salesperson_filter(self.db, self.user, r.salesperson.id if r.salesperson else None)
        return service.Filters(
            organization_id=self.user.organization_id,
            start=start,
            end=end,
            salesperson_ids=None if scope is None else frozenset(scope),
            territory_id=r.territory.id if r.territory else None,
            product_id=r.product.id if r.product else None,
            customer_id=r.customer.id if r.customer else None,
        )

    def scope_label(self, r: Resolved) -> str:
        if r.salesperson:
            who = r.salesperson.name if r.salesperson.id != self.user.id else "you"
        elif self.user.role in ORG_WIDE_ROLES:
            who = self.org.name
        elif self.user.role == Role.REGIONAL_MANAGER:
            who = "your region"
        elif self.user.role == Role.SALES_MANAGER:
            who = "your team"
        else:
            who = "you"
        parts = [who]
        if r.territory:
            parts.append(f"in {r.territory.name}")
        if r.product:
            parts.append(f"for {r.product.name}")
        return " ".join(parts)

    def period(self, spec: PeriodSpec | None, default: PeriodSpec | None = None) -> tuple[date, date, str]:
        return resolve(spec or default, self.today)

    def as_of(self) -> date | None:
        return service.data_as_of(self.db, self.user.organization_id)

    # --- dispatch ------------------------------------------------------------------------------

    def run(self, interp: Interpretation) -> Facts:
        if interp.intent in (Intent.GREETING, Intent.HELP, Intent.UNKNOWN):
            return Facts(intent=interp.intent, title="", scope="", suggestions=STARTERS)
        r = _resolve_entities(interp, self.catalog)
        handler = getattr(self, f"_{interp.intent.name.lower()}")
        facts = handler(interp, r)
        facts.data_as_of = facts.data_as_of or self.as_of()
        return facts

    # --- intents -------------------------------------------------------------------------------

    def _sales(self, interp: Interpretation, r: Resolved) -> Facts:
        start, end, label = self.period(interp.period)
        o = service.overview(self.db, self.filters(start, end, r), self.today)
        facts = Facts(
            intent=Intent.SALES,
            title=f"Sales · {label}",
            scope=self.scope_label(r),
            period=(start, end),
            comparison=(o["comparison_period"]["start"], o["comparison_period"]["end"]) if o["comparison_available"] else None,
            figures={
                "revenue": o["revenue"],
                "orders": o["orders"],
                "average_order_value": o["average_order_value"],
                "buying_customers": o["active_customers"],
                "previous_revenue": o["previous_revenue"] if o["comparison_available"] else None,
                "revenue_growth_pct": o["revenue_growth_pct"],
                "target": o["target"] if (start.year, start.month) == (end.year, end.month) else None,
                "achievement_pct": o["achievement_pct"] if (start.year, start.month) == (end.year, end.month) else None,
            },
            suggestions=["Am I on track for my target?", "Top 5 customers", "Compare with last month"],
        )
        if o["orders"] == 0:
            facts.notes.append("No sales recorded in this period.")
        return facts

    def _target(self, interp: Interpretation, r: Resolved, projection_focus: bool = False) -> Facts:
        _, end, _ = self.period(interp.period)
        start = month_start(end)  # targets are monthly
        label = "this month" if (end.year, end.month) == (self.today.year, self.today.month) else f"{end:%B %Y}"
        f = self.filters(start, end, r)
        o = service.overview(self.db, f, self.today)
        in_progress = start <= self.today <= month_end(start) and end == self.today
        days_in_month = (month_end(start) - start).days + 1
        days_elapsed = (end - start).days + 1
        projection = metrics.straight_line_projection(o["revenue"], days_elapsed, days_in_month) if in_progress else None
        facts = Facts(
            intent=Intent.PROJECTION if projection_focus else Intent.TARGET,
            title=f"{'Month-end outlook' if projection_focus else 'Target'} · {label}",
            scope=self.scope_label(r),
            period=(start, end),
            figures={
                "revenue": o["revenue"],
                "target": o["target"],
                "achievement_pct": o["achievement_pct"],
                "target_gap": o["target_gap"],
                "required_daily_run_rate": o["required_daily_run_rate"],
                "days_left": (month_end(start) - self.today).days + 1 if in_progress else 0,
                "projected_revenue": projection,
                "projected_achievement_pct": metrics.achievement_pct(projection, o["target"]) if projection is not None else None,
                "days_elapsed": days_elapsed,
                "days_in_month": days_in_month,
            },
            suggestions=["Who should I follow up with?", "Top 5 customers this month", "Compare with last month"],
        )
        if o["target"] is None:
            facts.notes.append("No target is set for this period" + (" (targets aren't split by product or territory)." if (r.product or r.territory or r.customer) else "."))
        if projection is not None:
            facts.notes.append("The outlook assumes the pace so far continues; it is not a forecast model.")
        if o["target"] is not None and o["target_gap"] is not None and o["target_gap"] > 0 and in_progress:
            facts.actions.append("Focus on customers who are due to reorder: ask “who should I follow up with?”")
        return facts

    def _projection(self, interp: Interpretation, r: Resolved) -> Facts:
        return self._target(interp, r, projection_focus=True)

    def _compare(self, interp: Interpretation, r: Resolved) -> Facts:
        start, end, label = self.period(interp.period)
        if interp.compare_to:
            p_start, p_end, p_label = resolve(interp.compare_to, self.today)
        else:
            p_start, p_end = previous_period(start, end)
            p_label = "the previous period"
        f = self.filters(start, end, r)
        cur, cur_orders, _ = service.revenue_totals(self.db, f)
        prev, prev_orders, _ = service.revenue_totals(self.db, replace(f, start=p_start, end=p_end))
        facts = Facts(
            intent=Intent.COMPARE,
            title=f"{label.capitalize()} vs {p_label}",
            scope=self.scope_label(r),
            period=(start, end),
            comparison=(p_start, p_end),
            figures={
                "revenue": cur,
                "previous_revenue": prev,
                "change": cur - prev,
                "revenue_growth_pct": metrics.growth_pct(cur, prev),
                "orders": cur_orders,
                "previous_orders": prev_orders,
                "orders_growth_pct": metrics.growth_pct(Decimal(cur_orders), Decimal(prev_orders)),
            },
            suggestions=["Top 5 customers", "Which products are declining?", "Am I on track for my target?"],
        )
        if prev == 0:
            facts.notes.append("There are no sales in the comparison period, so growth can't be calculated.")
        days, p_days = (end - start).days + 1, (p_end - p_start).days + 1
        if abs(days - p_days) > 3:
            facts.notes.append(f"Note: {days} days are compared with {p_days} days.")
        return facts

    def _ranked(self, interp: Interpretation, r: Resolved, kind: str) -> Facts:
        start, end, label = self.period(interp.period)
        f = self.filters(start, end, r)
        limit = interp.limit or 5
        bottom = interp.direction == "bottom"
        fetch = service.top_customers if kind == "customers" else service.top_products
        rows = fetch(self.db, f, 100 if bottom else limit)
        if bottom:
            rows = list(reversed(rows))[:limit]
        total, _, _ = service.revenue_totals(self.db, f)
        word = "Smallest" if bottom else "Top"
        return Facts(
            intent=Intent.TOP_CUSTOMERS if kind == "customers" else Intent.TOP_PRODUCTS,
            title=f"{word} {len(rows)} {kind} · {label}",
            scope=self.scope_label(r),
            period=(start, end),
            figures={"total_revenue": total},
            rows=[
                {
                    "name": row["name"],
                    "revenue": row["revenue"],
                    "share_pct": row["share_pct"],
                    **({"orders": row["orders"], "last_order_date": row["last_order_date"]} if kind == "customers"
                       else {"units": row["units"], "orders": row["orders"]}),
                }
                for row in rows
            ],
            notes=[] if rows else ["No sales recorded in this period."],
            suggestions=(["Which customers have gone quiet?", "Compare with last month"] if kind == "customers"
                         else ["Which products are declining?", "Top 5 customers"]),
        )

    def _top_customers(self, interp, r):
        return self._ranked(interp, r, "customers")

    def _top_products(self, interp, r):
        return self._ranked(interp, r, "products")

    def _customer(self, interp: Interpretation, r: Resolved) -> Facts:
        if r.customer is None:
            return Facts(intent=Intent.CUSTOMER, title="Which customer do you mean?", scope="", clarify=True,
                         notes=["Name the customer, e.g. “how much did Gupta Stores buy last month?”"])
        start, end, label = self.period(interp.period, PeriodSpec(kind="last_n_days", n=90))
        f = self.filters(start, end, r)
        o = service.overview(self.db, f, self.today)
        products = service.top_products(self.db, f, 3)
        health = insights.customer_health(self.db, self.filters(month_start(self.today), self.today, r))
        status = health["customers"][0] if health["customers"] else None
        return Facts(
            intent=Intent.CUSTOMER,
            title=f"{r.customer.name} · {label}",
            scope=self.scope_label(replace(r, customer=None)),
            period=(start, end),
            figures={
                "revenue": o["revenue"],
                "orders": o["orders"],
                "average_order_value": o["average_order_value"],
                "status": status["status"] if status else "never_ordered",
                "status_reason": status["reason"] if status else "No orders on record yet.",
                "last_order_date": status["last_order_date"] if status else None,
                "monthly_value": status["monthly_value"] if status else None,
            },
            rows=[{"name": p["name"], "revenue": p["revenue"], "units": p["units"]} for p in products],
            notes=[] if o["orders"] else ["No purchases in this period."],
            suggestions=["Compare with last month", "Who else should I follow up with?"],
        )

    def _product(self, interp: Interpretation, r: Resolved) -> Facts:
        start, end, label = self.period(interp.period, PeriodSpec(kind="last_n_months", n=3))
        f = self.filters(start, end, r)
        trends = insights.product_trends(self.db, f)
        row = trends["products"][0] if trends["products"] else None
        buyers = service.top_customers(self.db, f, 3)
        facts = Facts(
            intent=Intent.PRODUCT,
            title=f"{r.product.name} · {label}",
            scope=self.scope_label(replace(r, product=None)),
            period=(start, end),
            comparison=(trends["comparison_period"]["start"], trends["comparison_period"]["end"]) if trends["comparison_available"] else None,
            figures={
                "revenue": row["revenue"] if row else 0,
                "units": row["units"] if row else 0,
                "buyers": row["buyers"] if row else 0,
                "previous_revenue": row["previous_revenue"] if row and trends["comparison_available"] else None,
                "revenue_growth_pct": row["growth_pct"] if row else None,
                "trend": row["trend"] if row else None,
                "share_pct": row["share_pct"] if row else None,
            },
            rows=[{"name": b["name"], "revenue": b["revenue"]} for b in buyers],
            suggestions=["Which products are declining?", "Best selling products this month"],
        )
        if not row:
            facts.notes.append("No sales of this product in the period.")
        return facts

    def _follow_up(self, interp: Interpretation, r: Resolved) -> Facts:
        f = self.filters(month_start(self.today), self.today, r)
        overdue = insights.follow_ups(self.db, f, self.today, "overdue", limit=5)
        due_today = [a for a in insights.follow_ups(self.db, f, self.today, "upcoming", limit=20) if a["due_date"] == self.today]
        health = insights.customer_health(self.db, f)
        quiet = [c for c in health["customers"] if c["status"] in ("at_risk", "dormant")]
        quiet.sort(key=lambda c: (c["status"] != "at_risk", -c["monthly_value"]))  # at-risk first: still winnable
        limit = interp.limit or 6

        # Promises already made come first, then customers drifting away (half the list each at most,
        # so neither crowds out the other).
        promised = [{"kind": "follow_up", "name": a["customer_name"] or "—", "activity": a["activity_type"].value,
                     "due_date": a["due_date"], "days_overdue": a["days_overdue"], "notes": a["notes"]}
                    for a in overdue + due_today]
        drifting = [{"kind": c["status"], "name": c["name"], "days_since_last_order": c["days_since_last_order"],
                     "typical_gap_days": c["typical_gap_days"], "monthly_value": c["monthly_value"]}
                    for c in quiet if c["name"] not in {p["name"] for p in promised}]
        half = max(1, limit // 2)
        rows = promised[: max(half, limit - len(drifting))]
        rows += drifting[: limit - len(rows)]
        return Facts(
            intent=Intent.FOLLOW_UP,
            title="Who to follow up with today",
            scope=self.scope_label(r),
            figures={"overdue_follow_ups": len(overdue), "due_today": len(due_today),
                     "at_risk_customers": health["summary"]["at_risk"], "dormant_customers": health["summary"]["dormant"]},
            rows=rows,
            notes=[] if rows else ["Nothing urgent: no overdue follow-ups and every customer is ordering on their usual rhythm."],
            suggestions=["Which deals are stalled?", "Am I on track for my target?"],
        )

    def _customer_health(self, interp: Interpretation, r: Resolved) -> Facts:
        _, end, _ = self.period(interp.period)
        f = self.filters(month_start(end), end, r)
        health = insights.customer_health(self.db, f)
        quiet = [c for c in health["customers"] if c["status"] != "active"][: interp.limit or 8]
        s = health["summary"]
        return Facts(
            intent=Intent.CUSTOMER_HEALTH,
            title="Customers who have gone quiet",
            scope=self.scope_label(r),
            figures={"active": s["active"], "at_risk": s["at_risk"], "dormant": s["dormant"],
                     "monthly_value_at_risk": s["monthly_value_at_risk"], "dormant_days": health["rules"]["dormant_days"]},
            rows=[{"name": c["name"], "status": c["status"], "days_since_last_order": c["days_since_last_order"],
                   "typical_gap_days": c["typical_gap_days"], "monthly_value": c["monthly_value"]} for c in quiet],
            notes=[] if quiet else ["Every customer is ordering on their usual rhythm."],
            suggestions=["Who should I follow up with today?", "Top 5 customers"],
        )

    def _pipeline(self, interp: Interpretation, r: Resolved) -> Facts:
        start, end, label = self.period(interp.period, PeriodSpec(kind="last_n_months", n=3))
        p = insights.pipeline(self.db, self.filters(start, end, r), self.today)
        flagged = [d for d in p["deals"] if d["stalled"] or d["overdue"]]
        deals = (flagged or p["deals"])[: interp.limit or 5]
        return Facts(
            intent=Intent.PIPELINE,
            title="Pipeline",
            scope=self.scope_label(r),
            period=(start, end),
            figures={"open_count": p["open_count"], "open_value": p["open_value"], "weighted_value": p["weighted_value"],
                     "stalled_count": p["stalled_count"], "overdue_count": p["overdue_count"], "won_count": p["won_count"],
                     "lost_count": p["lost_count"], "win_rate_pct": p["win_rate_pct"], "stalled_days": p["rules"]["stalled_days"]},
            rows=[{"name": d["name"], "customer": d["customer_name"], "stage": d["stage"].value, "amount": d["amount"],
                   "stalled": d["stalled"], "overdue": d["overdue"], "days_idle": d["days_idle"],
                   "expected_close": d["expected_close"]} for d in deals],
            notes=["Won, lost and win rate are for deals closed in " + label + "."],
            suggestions=["Who should I follow up with?", "Am I on track for my target?"],
        )

    def _team(self, interp: Interpretation, r: Resolved) -> Facts:
        start, end, label = self.period(interp.period)
        rows = service.leaderboard(self.db, self.filters(start, end, r))
        bottom = interp.direction == "bottom"
        if bottom:
            rows = sorted(rows, key=lambda x: (x["achievement_pct"] is None, x["achievement_pct"] or 0))
        facts = Facts(
            intent=Intent.TEAM,
            title=f"{'Who needs support' if bottom else 'Leaderboard'} · {label}",
            scope=self.scope_label(r),
            period=(start, end),
            rows=[{"name": x["name"], "revenue": x["revenue"], "target": x["target"], "achievement_pct": x["achievement_pct"],
                   "rank": x["rank"]} for x in rows[: interp.limit or 8]],
            suggestions=["Show team activity", "Which deals are stalled?"],
        )
        if len(rows) <= 1:
            facts.notes.append("You can only see your own numbers, so there's no team ranking to show.")
        if bottom:
            facts.notes.append("Sorted by target achievement, lowest first.")
        return facts

    def _product_trends(self, interp: Interpretation, r: Resolved) -> Facts:
        start, end, label = self.period(interp.period, PeriodSpec(kind="last_n_months", n=3))
        t = insights.product_trends(self.db, self.filters(start, end, r))
        products = [p for p in t["products"] if p["growth_pct"] is not None]
        falling = sorted([p for p in products if p["growth_pct"] < 0], key=lambda p: p["growth_pct"])
        rising = sorted([p for p in products if p["growth_pct"] > 0], key=lambda p: -p["growth_pct"])
        n = interp.limit or 3
        pick = falling[:n] if interp.direction == "bottom" else rising[:n] + falling[:n] if interp.direction is None else rising[:n]
        facts = Facts(
            intent=Intent.PRODUCT_TRENDS,
            title=f"Product trends · {label}",
            scope=self.scope_label(r),
            period=(start, end),
            comparison=(t["comparison_period"]["start"], t["comparison_period"]["end"]),
            figures={"rising": t["counts"].get("rising", 0), "declining": t["counts"].get("declining", 0) + t["counts"].get("stopped", 0)},
            rows=[{"name": p["name"], "revenue": p["revenue"], "previous_revenue": p["previous_revenue"],
                   "growth_pct": p["growth_pct"], "trend": p["trend"]} for p in pick],
            suggestions=["Best selling products this month", "Top 5 customers"],
        )
        if not t["comparison_available"]:
            facts.notes.append("There's no data for the comparison period yet, so trends can't be shown.")
            facts.rows = []
        return facts

    def _briefing(self, interp: Interpretation, r: Resolved) -> Facts:
        yesterday = self.today - timedelta(days=1)
        sections = [
            self._sales(Interpretation(intent=Intent.SALES, period=PeriodSpec(kind="yesterday")), r),
            self._target(Interpretation(intent=Intent.TARGET), r),
            self._follow_up(Interpretation(intent=Intent.FOLLOW_UP, limit=4), r),
        ]
        sections[0].title = f"Yesterday ({yesterday.day} {yesterday:%b})"
        for section in sections:
            section.scope = ""  # stated once, at the top of the briefing
        return Facts(
            intent=Intent.BRIEFING,
            title=f"Your briefing for {self.today.day} {self.today:%b %Y}",
            scope=self.scope_label(r),
            sections=sections,
            suggestions=["Which deals are stalled?", "Top 5 customers this month"],
        )


STARTERS = [
    "How much have I sold this month?",
    "Am I on track for my target?",
    "Who should I follow up with today?",
    "Top 5 customers this month",
    "Which products are declining?",
    "Compare this month with last month",
]
