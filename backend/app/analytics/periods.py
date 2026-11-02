import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def org_today(timezone: str) -> date:
    return datetime.now(ZoneInfo(timezone)).date()


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, months: int) -> date:
    """Shift by whole months, clamping the day (31 Mar - 1 month = 28/29 Feb)."""
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    day = min(d.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


def previous_period(start: date, end: date) -> tuple[date, date]:
    """The comparison period for growth.

    Month-to-date or a whole month compares with the same days of the previous month
    (1-15 Nov vs 1-15 Oct; all of Nov vs all of Oct). Any other range compares with the
    window of equal length immediately before it.
    """
    if start.day == 1 and (start.year, start.month) == (end.year, end.month):
        prev_start = add_months(start, -1)
        prev_end = month_end(prev_start) if end == month_end(end) else add_months(end, -1)
        return prev_start, prev_end
    length = end - start
    prev_end = start - timedelta(days=1)
    return prev_end - length, prev_end
