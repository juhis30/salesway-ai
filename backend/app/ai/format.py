"""Indian-style number formatting for replies (lakh and crore), matching the web app."""

from datetime import date
from decimal import Decimal


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    return float(Decimal(str(value)))


def _trim(n: float, digits: int) -> str:
    text = f"{n:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def group_indian(n: int) -> str:
    """1234567 -> 12,34,567"""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + s


def inr(value) -> str:
    """₹12,28,363"""
    n = _num(value)
    return "—" if n is None else f"₹{group_indian(round(n))}"


def inr_short(value) -> str:
    """₹1.23 Cr · ₹12.3 L · ₹45,600 (thousands stay exact: they read naturally)."""
    n = _num(value)
    if n is None:
        return "—"
    sign, a = ("−" if n < 0 else ""), abs(n)
    if a >= 1e7:
        return f"{sign}₹{_trim(a / 1e7, 2)} Cr"
    if a >= 1e5:
        return f"{sign}₹{_trim(a / 1e5, 2 if a < 1e6 else 1)} L"
    return f"{sign}₹{group_indian(round(a))}"


def pct(value, digits: int = 1) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}%"


def signed_pct(value) -> str:
    if value is None:
        return "—"
    v = float(value)
    return f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v):.1f}%"


def number(value) -> str:
    n = _num(value)
    return "—" if n is None else group_indian(round(n))


def plural(count, noun: str) -> str:
    n = _num(count)
    return f"{number(count)} {noun}{'' if n == 1 else 's'}"


def day(d: date | None) -> str:
    return "—" if d is None else f"{d.day} {d:%b %Y}"


def date_range(start: date, end: date) -> str:
    if start == end:
        return day(start)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {end:%b %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%b} – {day(end)}"
    return f"{day(start)} – {day(end)}"
