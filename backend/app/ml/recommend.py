"""Next best actions: concrete things each salesperson can do, ranked by rupees at stake.

Every action states the data behind it. Four kinds:
  win_back    a customer likely to stop ordering (churn model, or the at-risk rule if the model is off)
  follow_up   a visit or call that was promised and is now overdue
  revive_deal an open deal that has gone quiet
  cross_sell  a product most similar customers buy that this customer doesn't
"""

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.ai.format import inr_short

MAX_PER_USER = 8
MAX_PER_KIND = 3
CROSS_SELL_MIN_PEERS = 5
CROSS_SELL_MIN_ADOPTION = 0.6
SNOOZE_DAYS = 14  # a dismissed or completed action isn't suggested again for two weeks


@dataclass
class Action:
    user_id: uuid.UUID
    kind: str
    entity_type: str
    entity_id: uuid.UUID
    title: str
    reason: str
    expected_value: Decimal
    score: float | None = None
    details: dict = field(default_factory=dict)


def win_back(customers: list[dict]) -> list[Action]:
    """customers: owner, id, name, monthly_value, churn_probability (None = rule-based), signals."""
    out = []
    for c in customers:
        p = c["churn_probability"]
        value = Decimal(str(c["monthly_value"]))
        if p is not None:
            if p < 0.5:
                continue
            reason = f"{round(p * 100)}% chance they stop ordering in the next 60 days: " + "; ".join(c["signals"]) + "."
            expected = (value * Decimal(str(p))).quantize(Decimal("0.01"))
        else:
            reason = "Ordering less often than usual: " + "; ".join(c["signals"]) + "."
            expected = value
        out.append(Action(c["owner"], "win_back", "customer", c["id"], f"Call {c['name']} before they drift away",
                          reason + f" Usually worth {inr_short(value)} a month.", expected, p))
    return out


def follow_ups(items: list[dict], monthly_value: dict[uuid.UUID, float]) -> list[Action]:
    out = []
    seen: set[tuple] = set()
    for a in sorted(items, key=lambda x: -x["days_overdue"]):
        key = (a["user_id"], a["customer_id"] or a["id"])
        if key in seen:
            continue  # one action per customer: the most overdue promise
        seen.add(key)
        value = Decimal(str(monthly_value.get(a["customer_id"], 0)))
        what = a["activity_type"].value
        out.append(Action(a["user_id"], "follow_up", "activity", a["id"],
                          f"Do the promised {what} to {a['customer_name']}" if a["customer_name"] else f"Do the promised {what}",
                          f"It was due {a['days_overdue']} days ago." + (f" Note: {a['notes']}." if a["notes"] else "")
                          + (f" The customer is worth {inr_short(value)} a month." if value else ""),
                          value))
    return out


def revive_deals(deals: list[dict], win_probability: dict[uuid.UUID, float], stage_weights: dict) -> list[Action]:
    out = []
    for d in deals:
        if not d["stalled"] or not d["owner_id"]:
            continue
        p = win_probability.get(d["opportunity_id"])
        weight = p if p is not None else float(stage_weights[d["stage"]])
        basis = f"{round(p * 100)}% estimated win chance" if p is not None else f"{round(weight * 100)}% stage weight"
        out.append(Action(d["owner_id"], "revive_deal", "opportunity", d["opportunity_id"],
                          f"Revive “{d['name']}”",
                          f"{inr_short(d['amount'])} deal, quiet for {d['days_idle']} days ({basis}).",
                          (Decimal(str(d["amount"])) * Decimal(str(weight))).quantize(Decimal("0.01")), p))
    return out


def cross_sell(recent_orders: dict[uuid.UUID, list[tuple[date, uuid.UUID, float]]], customers: dict[uuid.UUID, dict],
               products: dict[uuid.UUID, str], today: date) -> list[Action]:
    """recent_orders: customer -> [(day, product, amount)] for the last 180 days."""
    window = today - timedelta(days=90)
    by_segment: dict[str, list[uuid.UUID]] = defaultdict(list)
    for cid, lines in recent_orders.items():
        if any(day > window for day, _, _ in lines) and cid in customers:
            by_segment[customers[cid]["segment"] or "Unknown"].append(cid)

    out = []
    for segment, members in by_segment.items():
        if len(members) < CROSS_SELL_MIN_PEERS:
            continue
        adopters: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        spend: dict[uuid.UUID, list[float]] = defaultdict(list)
        for cid in members:
            per_product = Counter()
            for day, product, amount in recent_orders[cid]:
                if day > window:
                    per_product[product] += amount
            for product, amount in per_product.items():
                adopters[product].add(cid)
                spend[product].append(amount / 3)  # monthly
        for cid in members:
            bought = {product for _, product, _ in recent_orders[cid]}
            best = None
            for product, buyers in adopters.items():
                share = len(buyers) / len(members)
                if product in bought or share < CROSS_SELL_MIN_ADOPTION:
                    continue
                typical = sorted(spend[product])[len(spend[product]) // 2]
                if best is None or typical > best[2]:
                    best = (product, len(buyers), typical)
            if best and customers[cid]["owner"]:
                product, n, typical = best
                out.append(Action(customers[cid]["owner"], "cross_sell", "customer", cid,
                                  f"Offer {products[product]} to {customers[cid]['name']}",
                                  f"{n} of {len(members)} similar {segment} customers buy it (typically "
                                  f"{inr_short(typical)} a month); {customers[cid]['name']} hasn't in 6 months.",
                                  Decimal(str(round(typical * n / len(members), 2))), round(n / len(members), 3),
                                  {"product_id": str(product)}))
    return out


# Values differ in meaning by kind (a customer's monthly spend vs a weighted deal), so they are only
# compared within a kind. Across kinds the order is fixed and stated: promises already made, then
# customers drifting away, then stalled deals, then new sales.
KIND_ORDER = {"follow_up": 0, "win_back": 1, "revive_deal": 2, "cross_sell": 3}


def rank(actions: list[Action], snoozed: set[tuple[str, uuid.UUID]]) -> list[Action]:
    by_user: dict[uuid.UUID, list[Action]] = defaultdict(list)
    for a in actions:
        if (a.kind, a.entity_id) not in snoozed:
            by_user[a.user_id].append(a)
    chosen = []
    for items in by_user.values():
        picked: list[Action] = []
        for kind in KIND_ORDER:
            of_kind = sorted((a for a in items if a.kind == kind), key=lambda x: x.expected_value, reverse=True)
            picked += of_kind[:MAX_PER_KIND]
        chosen += picked[:MAX_PER_USER]
    return chosen
