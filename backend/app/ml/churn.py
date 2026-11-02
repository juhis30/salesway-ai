"""Customer churn: the chance an active customer places no order in the next 60 days.

Training examples are snapshots of the past: at a cut-off date every 14 days, each customer who was
still active is described by features computed from orders up to that date only, and labelled by
whether they ordered in the following 60 days. Models are compared on customers they never saw
(grouped cross-validation) against the simple rule already used on the Customer health page, and
are only switched on if they beat it.
"""

import math
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HORIZON_DAYS = 60
SNAPSHOT_EVERY = 14
MIN_HISTORY_DAYS = 120
MIN_POSITIVES = 15
MIN_AUC = 0.65
MIN_GAIN_OVER_RULE = 0.02

NUMERIC = ["days_since_last", "gap_ratio", "orders_90d", "log_revenue_90d", "revenue_trend", "tenure_days", "products_90d"]


@dataclass
class Order:
    day: date
    revenue: float
    products: frozenset


def features_at(orders: list[Order], cutoff: date, fallback_gap: float) -> dict | None:
    """Describe a customer as of `cutoff` using only orders up to then. None = not an active customer."""
    past = [o for o in orders if o.day <= cutoff]
    if not past:
        return None
    last = max(o.day for o in past)
    days_since = (cutoff - last).days
    if days_since >= HORIZON_DAYS:
        return None  # already dormant: lost, not "at risk"
    days = sorted({o.day for o in past})
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    typical = statistics.mean(gaps) if len(gaps) >= 2 else fallback_gap
    recent = [o for o in past if o.day > cutoff - timedelta(days=90)]
    prior = [o for o in past if cutoff - timedelta(days=180) < o.day <= cutoff - timedelta(days=90)]
    rev_recent, rev_prior = sum(o.revenue for o in recent), sum(o.revenue for o in prior)
    return {
        "days_since_last": days_since,
        "typical_gap": typical,
        "gap_ratio": days_since / max(typical, 1.0),
        "orders_90d": len(recent),
        "log_revenue_90d": math.log1p(rev_recent),
        "revenue_90d": rev_recent,
        "revenue_trend": min(rev_recent / rev_prior, 3.0) if rev_prior > 0 else 1.0,
        "tenure_days": (cutoff - days[0]).days,
        "products_90d": len(set().union(*(o.products for o in recent))) if recent else 0,
    }


def _fallback_gap(history: dict[uuid.UUID, list[Order]], cutoff: date) -> float:
    gaps = []
    for orders in history.values():
        days = sorted({o.day for o in orders if o.day <= cutoff})
        if len(days) >= 3:
            gaps.append(statistics.mean((b - a).days for a, b in zip(days, days[1:])))
    return statistics.median(gaps) if gaps else 30.0


def build_snapshots(history: dict[uuid.UUID, list[Order]], segments: dict[uuid.UUID, str], today: date):
    """Rows of (customer, cutoff, features, label) for every cutoff whose 60-day outcome is known."""
    if not history:
        return []
    first = min(o.day for orders in history.values() for o in orders)
    cutoff = first + timedelta(days=MIN_HISTORY_DAYS)
    last_cutoff = today - timedelta(days=HORIZON_DAYS)
    rows = []
    while cutoff <= last_cutoff:
        fallback = _fallback_gap(history, cutoff)
        for customer, orders in history.items():
            feats = features_at(orders, cutoff, fallback)
            if feats is None:
                continue
            future = any(cutoff < o.day <= cutoff + timedelta(days=HORIZON_DAYS) for o in orders)
            rows.append({"customer": customer, "cutoff": cutoff, "segment": segments.get(customer) or "Unknown",
                         **feats, "churned": int(not future)})
        cutoff += timedelta(days=SNAPSHOT_EVERY)
    return rows


def _matrix(rows: list[dict], segment_names: list[str]) -> np.ndarray:
    return np.array([[r[name] for name in NUMERIC] + [1.0 if r["segment"] == s else 0.0 for s in segment_names] for r in rows])


def _candidates():
    return {
        "logistic_regression": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
        "gradient_boosting": lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05),
    }


def train(rows: list[dict]) -> dict:
    """Compare models with the rule on customers held out by group; return evaluation and the chosen one."""
    positives = sum(r["churned"] for r in rows)
    summary = {"rows": len(rows), "positives": positives, "customers": len({r["customer"] for r in rows})}
    if positives < MIN_POSITIVES or len(rows) - positives < MIN_POSITIVES:
        return {**summary, "status": "insufficient_data",
                "reason": f"Only {positives} past examples of a customer stopping; at least {MIN_POSITIVES} are needed."}

    segment_names = sorted({r["segment"] for r in rows})
    X, y = _matrix(rows, segment_names), np.array([r["churned"] for r in rows])
    groups = np.array([str(r["customer"]) for r in rows])
    folds = GroupKFold(n_splits=min(5, len(set(groups))))

    rule_scores = np.array([r["gap_ratio"] for r in rows])
    evaluation = {"rule": {"auc": round(float(roc_auc_score(y, rule_scores)), 3)}}
    oof = {}
    for name, build in _candidates().items():
        predictions = np.zeros(len(y))
        for train_idx, test_idx in folds.split(X, y, groups):
            if len(set(y[train_idx])) < 2:
                continue
            model = build().fit(X[train_idx], y[train_idx])
            predictions[test_idx] = model.predict_proba(X[test_idx])[:, 1]
        oof[name] = predictions
        top = np.argsort(-predictions)[: max(1, positives)]
        evaluation[name] = {
            "auc": round(float(roc_auc_score(y, predictions)), 3),
            "brier": round(float(brier_score_loss(y, predictions)), 4),
            # Of the customers ranked riskiest (as many as actually churned), how many really did.
            "precision_at_k": round(float(y[top].mean()), 3),
        }

    best = max(oof, key=lambda n: evaluation[n]["auc"])
    best_auc, rule_auc = evaluation[best]["auc"], evaluation["rule"]["auc"]
    deployed = best_auc >= MIN_AUC and best_auc >= rule_auc + MIN_GAIN_OVER_RULE
    final = _candidates()[best]().fit(X, y)
    params = {"features": NUMERIC, "segments": segment_names}
    if best == "logistic_regression":
        scaler, logit = final.named_steps["standardscaler"], final.named_steps["logisticregression"]
        names = NUMERIC + [f"segment={s}" for s in segment_names]
        params["coefficients"] = {n: round(float(c), 4) for n, c in zip(names, logit.coef_[0])}
    return {
        **summary,
        "status": "active" if deployed else "rejected",
        "algorithm": best,
        "evaluation": evaluation,
        "reason": None if deployed else _why_not(best_auc, rule_auc),
        "params": params,
        "model": final,
        "segment_names": segment_names,
    }


def _why_not(best_auc: float, rule_auc: float) -> str:
    if best_auc < MIN_AUC:
        return f"Best model's tested accuracy (AUC {best_auc}) is below the {MIN_AUC} minimum; the rule stays in use."
    return (f"Best model (AUC {best_auc}) is not clearly better than the existing rule (AUC {rule_auc}); "
            f"it needs to beat it by {MIN_GAIN_OVER_RULE}, so the rule stays in use.")


def score(model, segment_names: list[str], current: list[dict]) -> list[float]:
    if not current:
        return []
    return [float(p) for p in model.predict_proba(_matrix(current, segment_names))[:, 1]]


def signals(f: dict) -> list[str]:
    """Plain-language reasons from the customer's own numbers (shown with every score)."""
    out = []
    if f["gap_ratio"] >= 1.5:
        out.append(f"{f['days_since_last']} days since last order; usually orders every {round(f['typical_gap'])} days")
    if f["revenue_trend"] <= 0.7:
        out.append(f"spend in the last 90 days is {round((1 - f['revenue_trend']) * 100)}% lower than the 90 days before")
    if f["orders_90d"] <= 2:
        out.append(f"only {f['orders_90d']} order{'s' if f['orders_90d'] != 1 else ''} in the last 90 days")
    if f["products_90d"] <= 2 and f["orders_90d"] > 0:
        out.append(f"buying a narrow range ({f['products_90d']} product{'s' if f['products_90d'] != 1 else ''})")
    return out or ["ordering on their usual rhythm"]


def group_orders(rows) -> dict[uuid.UUID, list[Order]]:
    """(customer_id, order_id, day, product_id, line_amount) rows -> orders per customer."""
    orders: dict[uuid.UUID, dict[uuid.UUID, list]] = defaultdict(dict)
    for customer, order_id, day, product, amount in rows:
        entry = orders[customer].setdefault(order_id, [day, 0.0, set()])
        entry[1] += float(amount)
        entry[2].add(product)
    return {c: [Order(d, r, frozenset(p)) for d, r, p in o.values()] for c, o in orders.items()}
