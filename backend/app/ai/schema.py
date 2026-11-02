"""The assistant's vocabulary: what a question can be about, and what an answer is built from.

A question becomes an `Interpretation` (from the mock parser or an LLM), is executed against the
analytics engine into `Facts`, and the reply is written from those facts only.
"""

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(str, enum.Enum):
    SALES = "sales_performance"
    TARGET = "target_status"
    PROJECTION = "month_end_projection"
    COMPARE = "compare_periods"
    TOP_CUSTOMERS = "top_customers"
    TOP_PRODUCTS = "top_products"
    CUSTOMER = "customer_summary"
    PRODUCT = "product_summary"
    FOLLOW_UP = "follow_up_priority"
    CUSTOMER_HEALTH = "customer_health"
    PIPELINE = "pipeline"
    TEAM = "team_performance"
    PRODUCT_TRENDS = "product_trends"
    BRIEFING = "daily_briefing"
    HELP = "help"
    GREETING = "greeting"
    UNKNOWN = "unknown"


INTENT_DESCRIPTIONS = {
    Intent.SALES: "Revenue, orders, average order value for a period ('my sales this month').",
    Intent.TARGET: "Target, achievement %, gap and required daily run rate ('am I on track?').",
    Intent.PROJECTION: "Expected month-end sales at the current pace ('expected month-end sales').",
    Intent.COMPARE: "Compare two periods ('compare this month with last month').",
    Intent.TOP_CUSTOMERS: "Ranked customers by revenue ('top 5 customers'; 'smallest buyers' = bottom).",
    Intent.TOP_PRODUCTS: "Ranked products by revenue ('best selling products').",
    Intent.CUSTOMER: "One named customer's purchases ('how much did Gupta Stores buy?').",
    Intent.PRODUCT: "One named product's sales ('how is Basmati Rice doing?').",
    Intent.FOLLOW_UP: "Who to follow up with / what to focus on today.",
    Intent.CUSTOMER_HEALTH: "Customers who stopped ordering, at risk or dormant.",
    Intent.PIPELINE: "Deals, pipeline value, stalled deals, win rate.",
    Intent.TEAM: "Salesperson ranking, best and under-performers.",
    Intent.PRODUCT_TRENDS: "Products rising or declining vs the previous period.",
    Intent.BRIEFING: "A daily summary: yesterday, month to date, priorities.",
    Intent.HELP: "What the assistant can do.",
    Intent.GREETING: "Hello / thanks.",
    Intent.UNKNOWN: "Anything else, including questions about data SaleSway doesn't have.",
}

PeriodKind = Literal[
    "today", "yesterday", "this_week", "last_week", "this_month", "last_month",
    "this_quarter", "last_quarter", "this_fy", "last_fy", "last_n_days", "last_n_months", "month", "range",
]


class PeriodSpec(BaseModel):
    """A time expression before it is turned into dates (so 'last month' stays relative)."""

    kind: PeriodKind
    n: int | None = Field(default=None, description="For last_n_days / last_n_months")
    month: int | None = Field(default=None, description="1-12, for kind=month")
    year: int | None = Field(default=None, description="For kind=month; omit for the most recent such month")
    start: date | None = Field(default=None, description="For kind=range")
    end: date | None = Field(default=None, description="For kind=range")
    same_period_last_year: bool = False


class Interpretation(BaseModel):
    """What the user asked, in structured form. Entity fields hold names exactly as the user wrote them."""

    intent: Intent
    period: PeriodSpec | None = None
    compare_to: PeriodSpec | None = None
    salesperson: str | None = Field(default=None, description="A person's name; omit for the user's own scope")
    customer: str | None = None
    product: str | None = None
    territory: str | None = None
    limit: int | None = Field(default=None, ge=1, le=20)
    direction: Literal["top", "bottom"] | None = Field(default=None, description="bottom = worst / smallest / declining")
    follows_previous: bool = Field(default=False, description="True if it continues the previous question ('and last month?')")
    language: Literal["en", "hi"] = "en"


@dataclass
class Facts:
    """Everything an answer may say. Numbers in a reply must come from here."""

    intent: Intent
    title: str
    scope: str
    period: tuple[date, date] | None = None
    comparison: tuple[date, date] | None = None
    figures: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    data_as_of: date | None = None
    sections: list["Facts"] = field(default_factory=list)  # the briefing combines several answers
    clarify: bool = False  # a question back to the user ("which Atta did you mean?"), not an answer


def to_jsonable(value: Any) -> Any:
    """Decimals -> strings, dates -> ISO, dataclasses/models -> dicts, recursively."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump(exclude_none=True))
    if isinstance(value, Facts):
        return {k: to_jsonable(v) for k, v in value.__dict__.items() if v not in (None, [], {})}
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "hex") and type(value).__name__ == "UUID":
        return str(value)
    return value
