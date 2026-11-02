"""Turn a PeriodSpec ('last month', 'October', 'last 7 days') into concrete dates."""

from datetime import date, timedelta

from app.ai.schema import PeriodSpec
from app.analytics.periods import add_months, month_end, month_start

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def _quarter_start(d: date) -> date:
    # Indian FY quarters (Apr-Jun, Jul-Sep, Oct-Dec, Jan-Mar) line up with calendar quarters.
    return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def resolve(spec: PeriodSpec | None, today: date) -> tuple[date, date, str]:
    """(start, end, label). Periods that include today end today; no spec means this month to date."""
    if spec is None:
        spec = PeriodSpec(kind="this_month")
    k = spec.kind

    if k == "today":
        start, end, label = today, today, "today"
    elif k == "yesterday":
        start = end = today - timedelta(days=1)
        label = "yesterday"
    elif k == "this_week":
        start, end, label = today - timedelta(days=today.weekday()), today, "this week"
    elif k == "last_week":
        end = today - timedelta(days=today.weekday() + 1)
        start, label = end - timedelta(days=6), "last week"
    elif k == "this_month":
        start, end, label = month_start(today), today, "this month"
    elif k == "last_month":
        start = add_months(month_start(today), -1)
        end, label = month_end(start), "last month"
    elif k == "this_quarter":
        start, end, label = _quarter_start(today), today, "this quarter"
    elif k == "last_quarter":
        start = add_months(_quarter_start(today), -3)
        end, label = add_months(start, 3) - timedelta(days=1), "last quarter"
    elif k == "this_fy":
        start, end, label = _fy_start(today), today, "this financial year"
    elif k == "last_fy":
        start = date(_fy_start(today).year - 1, 4, 1)
        end, label = date(start.year + 1, 3, 31), "last financial year"
    elif k == "last_n_days":
        n = max(1, min(spec.n or 7, 1095))
        start, end, label = today - timedelta(days=n - 1), today, f"the last {n} days"
    elif k == "last_n_months":
        n = max(1, min(spec.n or 3, 36))
        # Complete months only, matching the dashboard's "last N full months".
        start = add_months(month_start(today), -n)
        end, label = add_months(month_start(today), 0) - timedelta(days=1), f"the last {n} full months"
    elif k == "month":
        m = spec.month or today.month
        y = spec.year or (today.year if m <= today.month else today.year - 1)
        start = date(y, m, 1)
        end = min(month_end(start), today) if (y, m) == (today.year, today.month) else month_end(start)
        label = f"{MONTH_NAMES[m - 1]} {y}"
    elif k == "range" and spec.start and spec.end:
        start, end = min(spec.start, spec.end), max(spec.start, spec.end)
        label = "the selected dates"
    else:
        start, end, label = month_start(today), today, "this month"

    if spec.same_period_last_year:
        start, end = add_months(start, -12), add_months(end, -12)
        label = f"{label}, last year"
    return start, end, label
