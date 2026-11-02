"""Grounding check: every number in a reply must match a number in the facts it was written from.

Applied to language-model output. If the model introduces a figure the analytics engine didn't
produce, the reply is replaced by the template answer, which is grounded by construction.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.ai.schema import Facts

# ₹1.2 Cr · ₹8.5 L · 12,00,000 · 70.8% · 45K · 2026
NUMBER = re.compile(
    r"(?<![\w.])(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>cr|crore|crores|l|lakh|lakhs|lac|k|%)?(?![\w])",
    re.IGNORECASE,
)
SCALE = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "k": 1e3}


def _numbers_in_facts(value, out: set[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float, Decimal)):
        out.add(float(value))
        out.add(abs(float(value)))
    elif isinstance(value, date):
        out.update({value.day, value.month, value.year, value.year % 100})
    elif isinstance(value, str):
        for m in NUMBER.finditer(value):
            out.add(_value(m))
    elif isinstance(value, Facts):
        for v in value.__dict__.values():
            _numbers_in_facts(v, out)
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_in_facts(v, out)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _numbers_in_facts(v, out)


def _value(m: re.Match) -> float:
    try:
        n = float(Decimal(m.group("num").replace(",", "")))
    except InvalidOperation:
        return float("nan")
    unit = (m.group("unit") or "").lower()
    return n * SCALE.get(unit, 1)


def ungrounded_numbers(text: str, facts: Facts) -> list[str]:
    """Numbers in `text` that don't match any fact (allowing for rounding in ₹ L / Cr formatting)."""
    allowed: set[float] = set(range(0, 11))  # list numbering, "top 5", etc.
    _numbers_in_facts(facts, allowed)
    bad = []
    for m in NUMBER.finditer(text):
        v = _value(m)
        if v != v:  # NaN
            bad.append(m.group(0))
            continue
        # A compact amount like ₹8.5 L is a rounding of 8,49,912: allow ~1% (and ±1 for whole numbers).
        if not any(abs(v - a) <= max(abs(a) * 0.01, 1.0) for a in allowed):
            bad.append(m.group(0).strip())
    return bad
