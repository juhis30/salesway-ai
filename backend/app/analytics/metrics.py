"""Central business-metric definitions. Every screen and the AI copilot use these.

Revenue definition (documented in docs/metrics.md):
  * Revenue is NET of tax: sum of line quantity x unit price.
  * Only orders with status confirmed or completed count.
  * Pending, cancelled and returned orders are excluded.
  * An order counts in the period of its order_date.
"""

from decimal import Decimal

from app.models import OrderStatus

REVENUE_STATUSES = (OrderStatus.CONFIRMED, OrderStatus.COMPLETED)


def achievement_pct(actual: Decimal, target: Decimal | None) -> float | None:
    """Achievement % = actual sales / target x 100."""
    if not target:
        return None
    return round(float(actual / target * 100), 1)


def target_gap(target: Decimal | None, actual: Decimal) -> Decimal | None:
    """Target gap = target - actual. Negative means the target was exceeded."""
    if target is None:
        return None
    return target - actual


def average_order_value(revenue: Decimal, orders: int) -> Decimal | None:
    """AOV = revenue / number of orders."""
    if orders == 0:
        return None
    return (revenue / orders).quantize(Decimal("0.01"))


def growth_pct(current: Decimal, previous: Decimal) -> float | None:
    """Growth % = (current - previous) / previous x 100. Undefined when previous is zero."""
    if not previous:
        return None
    return round(float((current - previous) / previous * 100), 1)


def straight_line_projection(actual: Decimal, days_elapsed: int, days_in_period: int) -> Decimal | None:
    """Where the period ends if the pace so far continues: actual / days elapsed x days in period.

    A simple pace estimate, not a forecast model (those come with the predictive engine).
    """
    if days_elapsed <= 0 or days_in_period <= 0:
        return None
    return (actual / days_elapsed * days_in_period).quantize(Decimal("0.01"))


def required_daily_run_rate(gap: Decimal | None, days_remaining: int) -> Decimal | None:
    """Sales needed per remaining day to close the target gap."""
    if gap is None or gap <= 0 or days_remaining <= 0:
        return None
    return (gap / days_remaining).quantize(Decimal("0.01"))
