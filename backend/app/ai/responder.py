"""Write a reply from Facts using templates. Every number shown is a value from the facts.

Formatting is WhatsApp-friendly: *bold*, short lines, "•" bullets. The web app renders the same text.
"""

from app.ai.format import date_range, day, inr_short, number, pct, plural, signed_pct
from app.ai.schema import Facts, Intent

STATUS = {"active": "active", "at_risk": "at risk", "dormant": "dormant", "never_ordered": "no orders yet"}
TREND = {"rising": "▲ rising", "declining": "▼ declining", "steady": "steady", "new": "new", "stopped": "▼ stopped selling"}

HELP = (
    "I can answer questions about your sales numbers. Try:\n"
    "• How much have I sold this month?\n"
    "• Am I on track for my target?\n"
    "• Who should I follow up with today?\n"
    "• Top 5 customers last month\n"
    "• How much did Gupta Stores buy in October?\n"
    "• Which products are declining?\n"
    "• Which deals are stalled?\n"
    "• Compare this month with last month\n"
    "Follow-ups like “and last month?” work too. Hindi-English questions such as “is mahine kitna becha?” are fine."
)


def _vs(facts: Facts) -> str:
    return f"vs {date_range(*facts.comparison)}" if facts.comparison else ""


def _growth(value, facts: Facts) -> str:
    return f" ({signed_pct(value)} {_vs(facts)})" if value is not None and facts.comparison else ""


def render(facts: Facts, include_footer: bool = True) -> str:
    if facts.intent == Intent.GREETING:
        return "Hello! Ask me about your sales, targets, customers, products or deals. Type *help* for examples."
    if facts.intent == Intent.HELP:
        return HELP
    if facts.intent == Intent.UNKNOWN:
        return ("Sorry, I can't answer that one. I can only answer from your SaleSway sales data, "
                "and I don't guess.\n\n" + HELP)

    if facts.clarify:
        return "\n".join([facts.title, *[f"• {r['name']}" for r in facts.rows], *facts.notes])

    lines = [f"*{facts.title}*"]
    if facts.scope and facts.scope != "you":
        lines.append(f"_{facts.scope[0].upper()}{facts.scope[1:]}_")
    lines += BODY[facts.intent](facts)
    lines += [f"• {a}" for a in facts.actions]
    lines += facts.notes
    if include_footer and facts.data_as_of:
        lines.append(f"_Data up to {day(facts.data_as_of)} · revenue is net of tax_")
    return "\n".join(line for line in lines if line is not None)


def _sales(f: Facts) -> list[str]:
    g = f.figures
    if g["orders"] == 0:
        return []
    out = [
        f"• Revenue: *{inr_short(g['revenue'])}*{_growth(g['revenue_growth_pct'], f)}",
        f"• Orders: {plural(g['orders'], 'order')} · average {inr_short(g['average_order_value'])}",
        f"• Buying customers: {number(g['buying_customers'])}",
    ]
    if g.get("target") is not None:
        out.append(f"• Target: {inr_short(g['target'])} · {pct(g['achievement_pct'])} achieved")
    return out


def _target(f: Facts) -> list[str]:
    g = f.figures
    if g["target"] is None:
        return [f"• Sales so far: *{inr_short(g['revenue'])}*"]
    out = [
        f"• Target: {inr_short(g['target'])}",
        f"• Achieved: *{inr_short(g['revenue'])}* ({pct(g['achievement_pct'])})",
    ]
    gap = g["target_gap"]
    if gap is not None and gap > 0:
        out.append(f"• Remaining: {inr_short(gap)}")
        if g["required_daily_run_rate"] is not None:
            out.append(f"• Needed: {inr_short(g['required_daily_run_rate'])} a day for the {g['days_left']} days left")
    elif gap is not None:
        out.append(f"• Target exceeded by {inr_short(-gap)} 🎉")
    if g["projected_revenue"] is not None:
        verdict = "on track ✅" if g["projected_achievement_pct"] >= 100 else "at risk of missing the target ⚠️"
        out.append(f"• At the current pace you'd finish around *{inr_short(g['projected_revenue'])}* "
                   f"({pct(g['projected_achievement_pct'])} of target): {verdict}")
    return out


def _compare(f: Facts) -> list[str]:
    g = f.figures
    return [
        f"• {date_range(*f.period)}: *{inr_short(g['revenue'])}* · {plural(g['orders'], 'order')}",
        f"• {date_range(*f.comparison)}: {inr_short(g['previous_revenue'])} · {plural(g['previous_orders'], 'order')}",
        f"• Change: {inr_short(g['change'])} ({signed_pct(g['revenue_growth_pct'])})" if g["revenue_growth_pct"] is not None
        else f"• Change: {inr_short(g['change'])}",
    ]


def _ranked(f: Facts) -> list[str]:
    out = []
    for i, r in enumerate(f.rows, start=1):
        extra = plural(r["orders"], "order") if "last_order_date" in r else plural(r["units"], "unit")
        out.append(f"{i}. {r['name']}: *{inr_short(r['revenue'])}* ({pct(r['share_pct'])} · {extra})")
    return out


def _customer(f: Facts) -> list[str]:
    g = f.figures
    out = [
        f"• Bought: *{inr_short(g['revenue'])}* in {plural(g['orders'], 'order')}" if g["orders"] else "• No purchases in this period",
        f"• Status: {STATUS[g['status']]}. {g['status_reason']}",
    ]
    if g["monthly_value"] is not None:
        out.append(f"• Usually worth about {inr_short(g['monthly_value'])} a month")
    if f.rows:
        out.append("Top products: " + ", ".join(f"{r['name']} ({inr_short(r['revenue'])})" for r in f.rows))
    return out


def _product(f: Facts) -> list[str]:
    g = f.figures
    if not g["revenue"]:
        return []
    out = [
        f"• Revenue: *{inr_short(g['revenue'])}*{_growth(g['revenue_growth_pct'], f)}",
        f"• Units: {number(g['units'])} · bought by {number(g['buyers'])} customers",
    ]
    if g["trend"]:
        out.append(f"• Trend: {TREND[g['trend']]}")
    if f.rows:
        out.append("Biggest buyers: " + ", ".join(f"{r['name']} ({inr_short(r['revenue'])})" for r in f.rows))
    return out


def _follow_up(f: Facts) -> list[str]:
    out = []
    for i, r in enumerate(f.rows, start=1):
        if r["kind"] == "follow_up":
            when = f"{number(r['days_overdue'])} days overdue" if r["days_overdue"] else "due today"
            out.append(f"{i}. {r['name']}: promised {r['activity']} ({when})")
        else:
            gap = f"; usually every {number(r['typical_gap_days'])} days" if r["typical_gap_days"] else ""
            out.append(f"{i}. {r['name']}: no order for {number(r['days_since_last_order'])} days{gap} "
                       f"(worth ~{inr_short(r['monthly_value'])}/month)")
    return out


def _health(f: Facts) -> list[str]:
    g = f.figures
    out = [f"• {number(g['at_risk'])} at risk · {number(g['dormant'])} dormant ({number(g['dormant_days'])}+ days) · "
           f"{number(g['active'])} active",
           f"• Revenue at stake: *{inr_short(g['monthly_value_at_risk'])}* a month"]
    for r in f.rows:
        out.append(f"– {r['name']} ({STATUS[r['status']]}): {number(r['days_since_last_order'])} days since last order, "
                   f"~{inr_short(r['monthly_value'])}/month")
    return out


def _pipeline(f: Facts) -> list[str]:
    g = f.figures
    out = [
        f"• Open: *{inr_short(g['open_value'])}* in {plural(g['open_count'], 'deal')} (weighted {inr_short(g['weighted_value'])})",
        f"• Stalled: {number(g['stalled_count'])} (no activity {number(g['stalled_days'])}+ days) · past close date: {number(g['overdue_count'])}",
    ]
    if g["win_rate_pct"] is not None:
        out.append(f"• Win rate: {pct(g['win_rate_pct'])} ({number(g['won_count'])} won, {number(g['lost_count'])} lost)")
    for r in f.rows:
        flags = [x for x, on in (("stalled", r["stalled"]), ("past close date", r["overdue"])) if on]
        out.append(f"– {r['name']}: {inr_short(r['amount'])}, {r['stage']}" + (f" ({', '.join(flags)})" if flags else ""))
    return out


def _team(f: Facts) -> list[str]:
    return [
        f"{i}. {r['name']}: *{inr_short(r['revenue'])}*"
        + (f" · {pct(r['achievement_pct'])} of {inr_short(r['target'])}" if r["target"] is not None else "")
        for i, r in enumerate(f.rows, start=1)
    ]


def _trends(f: Facts) -> list[str]:
    g = f.figures
    out = [f"vs {date_range(*f.comparison)}: {number(g['rising'])} products rising, {number(g['declining'])} declining"] if f.rows else []
    for r in f.rows:
        out.append(f"• {r['name']}: {inr_short(r['revenue'])} ({signed_pct(r['growth_pct'])}, was {inr_short(r['previous_revenue'])})")
    return out


def _briefing(f: Facts) -> list[str]:
    out = []
    for section in f.sections:
        out.append("")
        out.append(render(section, include_footer=False))
    return out


BODY = {
    Intent.SALES: _sales,
    Intent.TARGET: _target,
    Intent.PROJECTION: _target,
    Intent.COMPARE: _compare,
    Intent.TOP_CUSTOMERS: _ranked,
    Intent.TOP_PRODUCTS: _ranked,
    Intent.CUSTOMER: _customer,
    Intent.PRODUCT: _product,
    Intent.FOLLOW_UP: _follow_up,
    Intent.CUSTOMER_HEALTH: _health,
    Intent.PIPELINE: _pipeline,
    Intent.TEAM: _team,
    Intent.PRODUCT_TRENDS: _trends,
    Intent.BRIEFING: _briefing,
}
