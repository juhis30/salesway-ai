"""Month-end and next-month sales forecasts, chosen by back-testing on the organisation's own history.

Back-testing replays the past: for every completed month and several "as of" days within it, each
method forecasts the month's total using only data up to that day, and is compared with what
actually happened. The method with the lowest average error is used, and its past errors give the
forecast range and the chance of reaching the target. Nothing here is a guess dressed up as a
probability: every range and percentage traces back to counted past errors.
"""

import statistics
from collections.abc import Callable
from datetime import date, timedelta

from app.analytics.periods import add_months, month_end, month_start

Series = dict[date, float]  # day -> revenue (missing days = 0)

CUT_DAYS = (3, 7, 10, 14, 17, 21, 24, 28)
WARMUP_DAYS = 56     # a method needs 8 weeks of history before it is tested
MIN_SAMPLES = 10     # fewer back-tests than this -> not enough data to forecast responsibly
BUCKET_MIN = 6       # error samples needed within a stage of the month, else all samples are used


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _total(series: Series, start: date, end: date) -> float:
    return sum(series.get(d, 0.0) for d in _days(start, end))


# --- Month-end methods: each returns the predicted month total, using data up to `cut` only ----


def pace(series: Series, cut: date) -> float:
    start = month_start(cut)
    elapsed = (cut - start).days + 1
    days_in_month = (month_end(cut) - start).days + 1
    return _total(series, start, cut) / elapsed * days_in_month


def recent_run_rate(series: Series, cut: date) -> float:
    so_far = _total(series, month_start(cut), cut)
    rate = _total(series, cut - timedelta(days=27), cut) / 28
    return so_far + rate * (month_end(cut) - cut).days


def weekday_profile(series: Series, cut: date) -> float:
    """So far + for each remaining day, the average of the last 8 same weekdays (Sundays stay ~0)."""
    so_far = _total(series, month_start(cut), cut)
    profile = {}
    for weekday in range(7):
        last = cut - timedelta(days=(cut.weekday() - weekday) % 7)
        profile[weekday] = statistics.mean(series.get(last - timedelta(weeks=w), 0.0) for w in range(8))
    return so_far + sum(profile[d.weekday()] for d in _days(cut + timedelta(days=1), month_end(cut)))


MONTH_END_METHODS: dict[str, Callable[[Series, date], float]] = {
    "pace": pace,
    "weekday_profile": weekday_profile,
    "recent_run_rate": recent_run_rate,
}
METHOD_LABELS = {
    "pace": "pace so far",
    "weekday_profile": "usual weekday pattern (last 8 weeks)",
    "recent_run_rate": "last 28 days' run rate",
}


def _stage(cut: date) -> str:
    fraction = cut.day / month_end(cut).day
    return "early" if fraction < 0.35 else "mid" if fraction < 0.7 else "late"


def backtest_month_end(series: Series, today: date) -> dict:
    """Relative errors (forecast / actual - 1) per method and month stage, over completed months."""
    if not series:
        return {"samples": 0}
    first = min(series)
    month = month_start(first + timedelta(days=WARMUP_DAYS))
    if month < first + timedelta(days=WARMUP_DAYS):
        month = add_months(month, 1)
    errors: dict[str, dict[str, list[float]]] = {m: {"early": [], "mid": [], "late": []} for m in MONTH_END_METHODS}
    months = 0
    while month_end(month) < month_start(today):
        actual = _total(series, month, month_end(month))
        if actual > 0:
            months += 1
            for day in CUT_DAYS:
                cut = month.replace(day=day)
                for name, method in MONTH_END_METHODS.items():
                    errors[name][_stage(cut)].append(method(series, cut) / actual - 1)
        month = add_months(month, 1)
    samples = sum(len(v) for v in errors["pace"].values())
    mape = {name: _mape([e for stage in buckets.values() for e in stage]) for name, buckets in errors.items()}
    return {"samples": samples, "months": months, "errors": errors, "mape": mape}


def _mape(errors: list[float]) -> float | None:
    return round(statistics.mean(abs(e) for e in errors) * 100, 1) if errors else None


def choose(backtest: dict) -> str | None:
    if backtest.get("samples", 0) < MIN_SAMPLES:
        return None
    return min(backtest["mape"], key=lambda m: backtest["mape"][m])


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def forecast_month_end(series: Series, today: date, method: str, errors: dict[str, list[float]], target: float | None) -> dict:
    """Forecast this month's total as of today, with an 80% range and the chance of reaching target.

    errors = the chosen method's past relative errors by stage of month.
    """
    point = MONTH_END_METHODS[method](series, today)
    stage = _stage(today)
    sample = errors.get(stage, [])
    if len(sample) < BUCKET_MIN:
        sample = [e for values in errors.values() for e in values]
    # actual = forecast / (1 + error): a forecast that ran 10% high in the past means actual was lower.
    outcomes = [point / (1 + e) for e in sample if e > -0.99]
    so_far = _total(series, month_start(today), today)
    lower = max(so_far, _quantile(outcomes, 0.10)) if outcomes else None
    upper = max(so_far, _quantile(outcomes, 0.90)) if outcomes else None
    chance = (sum(o >= target for o in outcomes) / len(outcomes)) if (outcomes and target) else None
    return {
        "method": method,
        "method_label": METHOD_LABELS[method],
        "so_far": so_far,
        "forecast": max(point, so_far),
        "lower": lower,
        "upper": upper,
        "chance_of_target": chance,
        "based_on_samples": len(outcomes),
        "stage": stage,
    }


# --- Next month ---------------------------------------------------------------------------------

NEXT_MONTH_METHODS = ("last_month", "mean_3_months", "trend_6_months")


def _next_month(method: str, history: list[float]) -> float:
    if method == "last_month":
        return history[-1]
    if method == "mean_3_months":
        return statistics.mean(history[-3:])
    recent = history[-6:]
    n = len(recent)
    x_mean, y_mean = (n - 1) / 2, statistics.mean(recent)
    denom = sum((i - x_mean) ** 2 for i in range(n)) or 1
    slope = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(recent)) / denom
    return max(0.0, y_mean + slope * (n - x_mean))


def monthly_totals(series: Series, today: date) -> list[tuple[date, float]]:
    """Totals of complete months only."""
    if not series:
        return []
    month, out = month_start(min(series)), []
    if min(series) > month:
        month = add_months(month, 1)  # the first month is partial
    while month_end(month) < month_start(today):
        out.append((month, _total(series, month, month_end(month))))
        month = add_months(month, 1)
    return out


def backtest_next_month(totals: list[float]) -> dict:
    errors: dict[str, list[float]] = {m: [] for m in NEXT_MONTH_METHODS}
    for i in range(6, len(totals)):
        if totals[i] <= 0:
            continue
        for m in NEXT_MONTH_METHODS:
            errors[m].append(_next_month(m, totals[:i]) / totals[i] - 1)
    return {"samples": len(errors["last_month"]), "errors": errors,
            "mape": {m: _mape(e) for m, e in errors.items()}}


def forecast_next_month(totals: list[float], method: str, errors: list[float]) -> dict:
    point = _next_month(method, totals)
    outcomes = [point / (1 + e) for e in errors if e > -0.99]
    return {
        "method": method,
        "forecast": point,
        # With only a handful of past months, show the full spread of past outcomes, not a percentile.
        "lower": min(outcomes) if outcomes else None,
        "upper": max(outcomes) if outcomes else None,
        "based_on_samples": len(outcomes),
    }
