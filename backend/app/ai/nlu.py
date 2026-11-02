"""Rule-based question understanding: the "mock" provider, used until an LLM key is configured.

Understands English and common Hinglish phrasing. Anything it can't place becomes UNKNOWN, and the
assistant says so rather than guessing.
"""

import re

from app.ai.catalog import Catalog, find
from app.ai.schema import Intent, Interpretation, PeriodSpec

HINGLISH = [
    (r"\b(is|iss|ish) (mahine|mahina|month)\b", "this month"),
    (r"\b(pichle|pichhle|pichla|pichhla|last) (mahine|mahina)\b", "last month"),
    (r"\b(is|iss) (hafte|hafta)\b", "this week"),
    (r"\b(pichle|pichhle) (hafte|hafta)\b", "last week"),
    (r"\b(is|iss) saal\b", "this year"),
    (r"\b(pichle|pichhle) saal\b", "last year"),
    (r"\baaj\b", "today"),
    (r"\bkal\b", "yesterday"),
    (r"\b(kitna|kitni|kitne)\b", "how much"),
    (r"\b(becha|bechi|beche|bikri|bikaa|bika)\b", "sales"),
    (r"\b(grahak|customer log)\b", "customers"),
    (r"\b(kisko|kise) (call|phone)\b", "who should i call"),
    (r"\b(kaun|kon)\b", "who"),
    (r"\b(mera|meri|mere)\b", "my"),
]
HINGLISH_MARKERS = re.compile(r"\b(mahine|mahina|hafte|saal|aaj|kitna|kitni|kitne|becha|bikri|grahak|mera|meri|mere|kya|hai|kaise|batao|kisko|kaun)\b")

MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
          "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
          "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "thirty": 30}

PERIOD_PATTERNS: list[tuple[str, callable]] = [
    (r"\b(last|past|previous) (\d+|\w+) days?\b", lambda m: PeriodSpec(kind="last_n_days", n=_n(m.group(2)))),
    (r"\b(last|past|previous) (\d+|\w+) months\b", lambda m: PeriodSpec(kind="last_n_months", n=_n(m.group(2)))),
    (r"\btoday\b", lambda m: PeriodSpec(kind="today")),
    (r"\byesterday\b", lambda m: PeriodSpec(kind="yesterday")),
    (r"\bthis week\b", lambda m: PeriodSpec(kind="this_week")),
    (r"\b(last|previous) week\b", lambda m: PeriodSpec(kind="last_week")),
    (r"\b(this month|mtd|month to date|current month)\b", lambda m: PeriodSpec(kind="this_month")),
    (r"\b(last|previous) month\b", lambda m: PeriodSpec(kind="last_month")),
    (r"\b(this|current) quarter\b", lambda m: PeriodSpec(kind="this_quarter")),
    (r"\b(last|previous) quarter\b", lambda m: PeriodSpec(kind="last_quarter")),
    (r"\b(this (financial )?year|this fy|ytd|year to date)\b", lambda m: PeriodSpec(kind="this_fy")),
    (r"\b(last|previous) (financial )?year\b|\blast fy\b", lambda m: PeriodSpec(kind="last_fy")),
    (r"\b(in |for |during |of )?(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b( (20\d\d))?",
     lambda m: _month(m)),
]

INTENT_RULES: list[tuple[Intent, str]] = [
    (Intent.HELP, r"\b(help|what can you do|how do i use|what can i ask|commands)\b"),
    (Intent.BRIEFING, r"\b(brief|briefing|daily update|morning update|summary|summarise|summarize|my day)\b"),
    (Intent.FOLLOW_UP, r"\b(follow ?up|followup|who should i (call|visit|contact|meet|chase)|which customers should i|focus on|what should i do|priorit|plan for today|next best action)"),
    (Intent.PIPELINE, r"\b(deal|deals|pipeline|opportunit\w*|stalled|win rate|closing)\b"),
    (Intent.CUSTOMER_HEALTH, r"\b(not ordered|haven t ordered|hasn t ordered|stopped (buying|ordering)|dormant|inactive|at risk|churn|lost customers|gone quiet|no orders?)\b"),
    (Intent.PROJECTION, r"\b(month ?end|expected|forecast\w*|projection|project|predict\w*|end of (the )?month|will i (make|reach|achieve|hit|close))\b"),
    (Intent.TARGET, r"\b(target|targets|achieve\w*|gap|how much more|remaining|on track|run rate|behind|quota)\b"),
    (Intent.PRODUCT_TRENDS, r"\b(declin\w*|falling|dropping|trend\w*|slow moving|rising|growing|increasing|improving|losing|performing (poorly|badly)|poorly)\b"),
    (Intent.TEAM, r"\b(team|leaderboard|salespeople|sales people|reps|executives|underperform\w*|top performer|best performer|ranking|rank|who is (the )?(best|top))\b"),
]
RANK_WORDS = r"\b(top|best|biggest|largest|highest|most|smallest|lowest|bottom|worst|least|rank\w*)\b"
SALES_WORDS = r"\b(sale|sales|sold|revenue|turnover|business|orders?|how much|performance|perform\w*|how am i doing|how are we doing|doing|achieved|numbers)\b"
COMPARE_WORDS = r"\b(compare\w*|vs|versus|than|against|growth|grew)\b"
FOLLOW_ON = r"^(and|what about|how about|aur|same for|now|also|then|ok and|okay and)\b"
PRONOUNS = r"\b((that|this|same) (customer|product|person|one)|him|her|them|they|it)\b"
GREETING = r"^(hi+|hello|hey|namaste|namaskar|good (morning|afternoon|evening)|thanks|thank you|thx|ok|okay|cool|great)( \w+)?$"
BOTTOM_WORDS = r"\b(worst|lowest|bottom|least|smallest|weakest|poor\w*|under ?perform\w*)\b"


FILLER = set("and what about how for the in of is s then same with vs versus compare to than now also aur ok okay please show me tell".split())


def _only_references(text: str, matches) -> bool:
    """True if nothing remains once period phrases, matched names and filler words are removed."""
    rest = text
    for pattern, _ in PERIOD_PATTERNS:
        rest = re.sub(pattern, " ", rest)
    words = {w for m in matches if m for w in m.matched}
    return all(w in FILLER or w in words or w.isdigit() for w in rest.split())


def _n(word: str) -> int:
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word, 3)


def _month(m: re.Match) -> PeriodSpec | None:
    name, year = m.group(2), m.group(4)
    # "may" is an ordinary word too: only treat it as the month with a year or "in/for May".
    if name == "may" and not (year or m.group(1)):
        return None
    return PeriodSpec(kind="month", month=MONTHS[name], year=int(year) if year else None)


def find_periods(text: str) -> list[PeriodSpec]:
    """All period expressions in order of appearance."""
    last_year = bool(re.search(r"\b(same (time|period) last year|last year same (time|period))\b", text))
    if last_year:
        text = re.sub(r"\b(same (time|period) last year|last year same (time|period))\b", " ", text)
    found: list[tuple[int, int, PeriodSpec]] = []
    for pattern, build in PERIOD_PATTERNS:
        for m in re.finditer(pattern, text):
            if any(s <= m.start() < e or s < m.end() <= e for s, e, _ in found):
                continue  # already covered by a longer expression
            spec = build(m)
            if spec is not None:
                found.append((m.start(), m.end(), spec))
    specs = [spec for _, _, spec in sorted(found, key=lambda f: f[0])]
    if last_year:
        # "this month vs same period last year" -> [this month, this month a year earlier]
        base = specs[0] if specs else PeriodSpec(kind="this_month")
        specs = [base, base.model_copy(update={"same_period_last_year": True})]
    return specs


def parse(message: str, catalog: Catalog) -> Interpretation:
    raw = message.strip()
    text = re.sub(r"[^\w\s%]", " ", raw.lower())
    text = re.sub(r"\s+", " ", text).strip()
    language = "hi" if HINGLISH_MARKERS.search(text) else "en"
    for pattern, replacement in HINGLISH:
        text = re.sub(pattern, replacement, text)

    if not text or re.fullmatch(GREETING, text):
        return Interpretation(intent=Intent.GREETING, language=language)

    periods = find_periods(text)
    period = periods[0] if periods else None
    compare_to = periods[1] if len(periods) > 1 else None
    wants_compare = bool(re.search(COMPARE_WORDS, text))
    joiner = re.search(r"\b(with|to|vs|versus|than|against)\b", text)
    if len(periods) == 1 and wants_compare and joiner and not find_periods(text[: joiner.start()]):
        # "compare with last month": the only period follows "with", so it's the comparison.
        compare_to, period = period, None

    customer = find(text, catalog.customers)
    product = find(text, catalog.products)
    person = find(text, catalog.people)
    territory = find(text, catalog.territories)
    # A word shared by a person and a customer ("Sharma") belongs to the better match.
    if person and customer and person.matched <= customer.matched:
        person = None
    if person and product and person.matched <= product.matched:
        person = None

    intent = Intent.UNKNOWN
    for candidate, pattern in INTENT_RULES:
        if re.search(pattern, text):
            intent = candidate
            break
    mentions_customers = re.search(r"\b(customers?|clients?|buyers?|accounts?|parties|party|retailers?|outlets?)\b", text)
    mentions_products = re.search(r"\b(products?|items?|skus?|selling|brands?)\b", text)
    ranking = re.search(RANK_WORDS, text) or re.search(r"\bbest ?selling\b", text)
    if intent == Intent.PRODUCT_TRENDS and not (mentions_products or product):
        intent = Intent.UNKNOWN if not re.search(SALES_WORDS, text) else Intent.COMPARE
    if intent == Intent.UNKNOWN:
        if mentions_customers and ranking:
            intent = Intent.TOP_CUSTOMERS
        elif (mentions_products and ranking) or re.search(r"\b(best|top) ?selling\b", text):
            intent = Intent.TOP_PRODUCTS
        elif compare_to or (wants_compare and (period or re.search(SALES_WORDS, text))):
            intent = Intent.COMPARE
        elif customer:
            intent = Intent.CUSTOMER
        elif product:
            intent = Intent.PRODUCT
        elif re.search(SALES_WORDS, text) or person or territory:
            intent = Intent.SALES

    limit_match = re.search(r"\b(top|bottom|best|worst|first)\s+(\d{1,2})\b", text) or re.search(r"\b(\d{1,2})\s+(customers|products|deals|items)\b", text)
    limit = None
    if limit_match:
        digits = next(g for g in limit_match.groups() if g and g.isdigit())
        limit = max(1, min(int(digits), 20))

    follows = bool(re.search(FOLLOW_ON, text) or re.search(PRONOUNS, text))
    explicit_comparison = period is not None and compare_to is not None
    if intent in (Intent.UNKNOWN, Intent.COMPARE) and not explicit_comparison and (period or compare_to or customer or product or person or territory):
        # A short message that is *only* a period or a name ("and October?", "what about Pune?")
        # continues the last question. One with other content ("weather today?") does not.
        follows = follows or _only_references(text, [customer, product, person, territory])
    if intent == Intent.PRODUCT_TRENDS and re.search(r"\b(rising|growing|grow\w*|up|increas\w*)\b", text) and not re.search(BOTTOM_WORDS + r"|\b(declin\w*|falling|dropping|losing)\b", text):
        direction = "top"
    elif re.search(BOTTOM_WORDS, text) or (intent == Intent.PRODUCT_TRENDS and re.search(r"\b(declin\w*|falling|dropping|losing)\b", text)):
        direction = "bottom"
    else:
        direction = None

    def name(match):
        return match.entities[0].name if match and not match.ambiguous else (
            " | ".join(e.name for e in match.entities[:4]) if match else None
        )

    return Interpretation(
        intent=intent,
        period=period,
        compare_to=compare_to,
        salesperson=name(person),
        customer=name(customer),
        product=name(product),
        territory=name(territory),
        limit=limit,
        direction=direction,
        follows_previous=follows,
        language=language,
    )


def merge_with_previous(current: Interpretation, previous: dict | None) -> Interpretation:
    """Fill a follow-up question ("and last month?", "compare with September") from the last one."""
    if not previous or not current.follows_previous:
        return current
    prev = Interpretation.model_validate(previous)
    if prev.intent in (Intent.HELP, Intent.GREETING, Intent.UNKNOWN):
        return current

    names_given = any([current.customer, current.product, current.salesperson, current.territory])
    entities = (
        dict(customer=current.customer, product=current.product, salesperson=current.salesperson, territory=current.territory)
        if names_given
        else dict(customer=prev.customer, product=prev.product, salesperson=prev.salesperson, territory=prev.territory)
    )
    intent = current.intent
    if intent == Intent.UNKNOWN:
        intent = prev.intent
        if names_given and prev.intent in (Intent.CUSTOMER, Intent.PRODUCT):
            intent = Intent.CUSTOMER if current.customer else Intent.PRODUCT if current.product else Intent.SALES
    if intent == Intent.SALES and not names_given:
        # "how much did that customer buy?" -> a question about the customer from last time.
        intent = Intent.CUSTOMER if entities["customer"] else Intent.PRODUCT if entities["product"] else intent
    period = current.period or prev.period
    if intent == Intent.COMPARE and current.period is None and prev.intent == Intent.COMPARE:
        period = prev.period
    return current.model_copy(
        update=dict(
            intent=intent,
            period=period,
            limit=current.limit or prev.limit,
            direction=current.direction or prev.direction,
            **entities,
        )
    )
