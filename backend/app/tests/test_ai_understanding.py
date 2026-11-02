"""The rule-based interpreter: periods, intents, names, Hinglish and follow-ups."""

import uuid
from datetime import date

import pytest

from app.ai.catalog import Catalog, Entity
from app.ai.guardrails import ungrounded_numbers
from app.ai.nlu import merge_with_previous, parse
from app.ai.periods import resolve
from app.ai.schema import Facts, Intent, PeriodSpec
from app.core.ratelimit import RateLimiter

TODAY = date(2026, 11, 2)


def entity(name, *aliases):
    return Entity(uuid.uuid4(), name, aliases)


CATALOG = Catalog(
    customers=[entity("Gupta Stores", "C1"), entity("Gupta Wholesale", "C2"), entity("Mehta Mart", "C3"), entity("Sharma Traders", "C4")],
    products=[entity("Chakki Atta 5kg", "SKU-001"), entity("Chakki Atta 10kg", "SKU-002"), entity("Basmati Rice 5kg", "SKU-003")],
    people=[entity("Amit Sharma"), entity("Harleen Kaur")],
    territories=[entity("Pune"), entity("Delhi NCR")],
)


@pytest.mark.parametrize(
    "spec, expected",
    [
        (PeriodSpec(kind="this_month"), (date(2026, 11, 1), date(2026, 11, 2))),
        (PeriodSpec(kind="last_month"), (date(2026, 10, 1), date(2026, 10, 31))),
        (PeriodSpec(kind="yesterday"), (date(2026, 11, 1), date(2026, 11, 1))),
        (PeriodSpec(kind="last_week"), (date(2026, 10, 26), date(2026, 11, 1))),  # Mon-Sun
        (PeriodSpec(kind="this_quarter"), (date(2026, 10, 1), date(2026, 11, 2))),
        (PeriodSpec(kind="last_quarter"), (date(2026, 7, 1), date(2026, 9, 30))),
        (PeriodSpec(kind="this_fy"), (date(2026, 4, 1), date(2026, 11, 2))),
        (PeriodSpec(kind="last_fy"), (date(2025, 4, 1), date(2026, 3, 31))),
        (PeriodSpec(kind="last_n_days", n=7), (date(2026, 10, 27), date(2026, 11, 2))),
        (PeriodSpec(kind="last_n_months", n=3), (date(2026, 8, 1), date(2026, 10, 31))),
        (PeriodSpec(kind="month", month=12), (date(2025, 12, 1), date(2025, 12, 31))),  # most recent December
        (PeriodSpec(kind="month", month=2, year=2026), (date(2026, 2, 1), date(2026, 2, 28))),
        (PeriodSpec(kind="this_month", same_period_last_year=True), (date(2025, 11, 1), date(2025, 11, 2))),
    ],
)
def test_period_resolution(spec, expected):
    assert resolve(spec, TODAY)[:2] == expected


@pytest.mark.parametrize(
    "question, intent",
    [
        ("How much have I sold this month?", Intent.SALES),
        ("Am I on track to achieve my monthly target?", Intent.TARGET),
        ("What is my expected month-end revenue?", Intent.PROJECTION),
        ("Which customers should I follow up with today?", Intent.FOLLOW_UP),
        ("Which customers have not ordered for 30 days?", Intent.CUSTOMER_HEALTH),
        ("Which deals are at risk?", Intent.PIPELINE),
        ("Which sales executives are underperforming?", Intent.TEAM),
        ("Which products are performing poorly?", Intent.PRODUCT_TRENDS),
        ("Top 5 customers?", Intent.TOP_CUSTOMERS),
        ("Best selling product?", Intent.TOP_PRODUCTS),
        ("Compare this month vs last month", Intent.COMPARE),
        ("how much did gupta stores buy in october", Intent.CUSTOMER),
        ("how is basmathi doing", Intent.PRODUCT),  # typo tolerated
        ("brief me", Intent.BRIEFING),
        ("hello", Intent.GREETING),
        ("help", Intent.HELP),
        ("what's the weather today?", Intent.UNKNOWN),
    ],
)
def test_intents(question, intent):
    assert parse(question, CATALOG).intent == intent


def test_hinglish():
    i = parse("is mahine kitna becha?", CATALOG)
    assert (i.intent, i.period.kind, i.language) == (Intent.SALES, "this_month", "hi")
    assert parse("pichle mahine ki bikri", CATALOG).period.kind == "last_month"


def test_names_and_ambiguity():
    i = parse("how much did Gupta Stores buy", CATALOG)
    assert i.customer == "Gupta Stores"
    # "gupta" alone matches two customers; "atta" two products: both reported, not guessed.
    assert parse("gupta sales", CATALOG).customer == "Gupta Stores | Gupta Wholesale"
    assert parse("atta sales", CATALOG).product == "Chakki Atta 5kg | Chakki Atta 10kg"
    assert parse("chakki atta 10 kg sales", CATALOG).product == "Chakki Atta 10kg"
    assert parse("SKU-003 sales last month", CATALOG).product == "Basmati Rice 5kg"
    # "Sharma" is both a person and a customer; the fuller match wins.
    i = parse("Sharma Traders orders", CATALOG)
    assert (i.customer, i.salesperson) == ("Sharma Traders", None)
    assert parse("how is Harleen doing", CATALOG).salesperson == "Harleen Kaur"


def test_comparisons():
    i = parse("compare this month with last month", CATALOG)
    assert (i.period.kind, i.compare_to.kind) == ("this_month", "last_month")
    i = parse("compare with last month", CATALOG)
    assert (i.intent, i.period, i.compare_to.kind, i.follows_previous) == (Intent.COMPARE, None, "last_month", True)
    i = parse("this month vs same period last year", CATALOG)
    assert i.compare_to.same_period_last_year is True


def test_follow_ups_inherit_context():
    previous = parse("how much did Gupta Stores buy in October", CATALOG).model_dump(mode="json")

    i = merge_with_previous(parse("and last month?", CATALOG), previous)
    assert (i.intent, i.customer, i.period.kind) == (Intent.CUSTOMER, "Gupta Stores", "last_month")

    i = merge_with_previous(parse("what about Mehta Mart?", CATALOG), previous)
    assert (i.intent, i.customer, i.period.kind) == (Intent.CUSTOMER, "Mehta Mart", "month")

    i = merge_with_previous(parse("compare with September", CATALOG), previous)
    assert (i.intent, i.customer, i.period.month, i.compare_to.month) == (Intent.COMPARE, "Gupta Stores", 10, 9)

    # A new topic is not glued onto the previous one.
    i = merge_with_previous(parse("what's the weather today?", CATALOG), previous)
    assert i.intent == Intent.UNKNOWN


def test_grounding_check():
    facts = Facts(intent=Intent.SALES, title="Sales", scope="you", period=(date(2026, 10, 1), date(2026, 10, 31)),
                  figures={"revenue": 849912, "orders": 37, "growth": -16.7})
    assert ungrounded_numbers("Revenue ₹8.5 L from 37 orders (−16.7%), 1–31 Oct 2026", facts) == []
    assert ungrounded_numbers("Revenue ₹8,49,912", facts) == []
    assert ungrounded_numbers("Revenue ₹12 L, up 40%", facts) == ["12 L", "40%"]


def test_rate_limiter():
    limiter = RateLimiter(limit=3)
    for _ in range(3):
        limiter.check("u1")
    with pytest.raises(Exception) as exc:
        limiter.check("u1")
    assert exc.value.status_code == 429
    limiter.check("u2")  # other users unaffected
