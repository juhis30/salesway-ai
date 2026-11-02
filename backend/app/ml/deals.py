"""Deal win probability, from facts known when the deal was opened.

Only information available at the time is used (deal size, source, customer type, whether they
already buy from you, the owner's win rate on deals closed *before* this one, planned length), so
the evaluation can't flatter itself with hindsight. The model is used only if it ranks won deals
above lost ones clearly better than the owner's track record alone.
"""

import math
import statistics
import uuid
from dataclasses import dataclass
from datetime import date

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MIN_CLOSED = 40
MIN_AUC = 0.65
MIN_GAIN = 0.02
PRIOR_STRENGTH = 4  # a new owner's win rate starts near the company average


@dataclass
class Deal:
    id: uuid.UUID
    owner: uuid.UUID | None
    amount: float
    source: str
    segment: str
    existing_customer: bool
    opened_on: date
    expected_close: date | None
    closed_on: date | None
    won: bool | None


def _owner_rate(deal: Deal, closed: list[Deal], as_of: date) -> float:
    before = [d for d in closed if d.closed_on and d.closed_on < as_of]
    overall = statistics.mean(d.won for d in before) if before else 0.4
    mine = [d.won for d in before if d.owner == deal.owner]
    return (sum(mine) + PRIOR_STRENGTH * overall) / (len(mine) + PRIOR_STRENGTH)


def features(deal: Deal, closed: list[Deal], as_of: date, sources: list[str], segments: list[str]) -> list[float]:
    planned = (deal.expected_close - deal.opened_on).days if deal.expected_close else 60
    return [
        math.log1p(deal.amount),
        _owner_rate(deal, closed, as_of),
        1.0 if deal.existing_customer else 0.0,
        float(planned),
        *[1.0 if deal.source == s else 0.0 for s in sources],
        *[1.0 if deal.segment == s else 0.0 for s in segments],
    ]


def train(closed: list[Deal]) -> dict:
    summary = {"rows": len(closed), "won": sum(d.won for d in closed)}
    if len(closed) < MIN_CLOSED or summary["won"] < 10 or len(closed) - summary["won"] < 10:
        return {**summary, "status": "insufficient_data",
                "reason": f"{len(closed)} closed deals; at least {MIN_CLOSED} with 10+ won and 10+ lost are needed."}
    sources = sorted({d.source for d in closed})
    segments = sorted({d.segment for d in closed})
    X = np.array([features(d, closed, d.opened_on, sources, segments) for d in closed])
    y = np.array([int(d.won) for d in closed])

    baseline = np.array([row[1] for row in X])  # owner's track record alone
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=7)
    predictions = np.zeros(len(y))
    for train_idx, test_idx in folds.split(X, y):
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5)).fit(X[train_idx], y[train_idx])
        predictions[test_idx] = model.predict_proba(X[test_idx])[:, 1]
    evaluation = {
        "owner_track_record": {"auc": round(float(roc_auc_score(y, baseline)), 3)},
        "logistic_regression": {"auc": round(float(roc_auc_score(y, predictions)), 3),
                                "brier": round(float(brier_score_loss(y, predictions)), 4)},
    }
    auc, base = evaluation["logistic_regression"]["auc"], evaluation["owner_track_record"]["auc"]
    deployed = auc >= MIN_AUC and auc >= base + MIN_GAIN
    final = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5)).fit(X, y)
    return {
        **summary,
        "status": "active" if deployed else "rejected",
        "algorithm": "logistic_regression",
        "evaluation": evaluation,
        "reason": None if deployed else (
            f"Tested accuracy (AUC {auc}) is below the {MIN_AUC} minimum; stage weights stay in use." if auc < MIN_AUC
            else f"Not clearly better than owners' track records (AUC {auc} vs {base}); stage weights stay in use."),
        "model": final,
        "sources": sources,
        "segments": segments,
    }


def score(trained: dict, open_deals: list[Deal], closed: list[Deal], today: date) -> list[float]:
    if not open_deals:
        return []
    X = np.array([features(d, closed, today, trained["sources"], trained["segments"]) for d in open_deals])
    return [float(p) for p in trained["model"].predict_proba(X)[:, 1]]
