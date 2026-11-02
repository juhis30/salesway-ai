"""Read side of the predictive models: used by the API and the assistant alike."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import service
from app.analytics.periods import month_end, month_start
from app.ml import forecast
from app.ml.train import active_model, daily_series
from app.models import MLModel, Prediction

KIND_LABELS = {
    "month_end_forecast": "Month-end sales forecast",
    "next_month_forecast": "Next month's sales forecast",
    "churn": "Customer churn risk",
    "deal_win": "Deal win probability",
}


def model_summary(model: MLModel | None) -> dict | None:
    if model is None:
        return None
    return {
        "id": model.id, "kind": model.kind, "label": KIND_LABELS.get(model.kind, model.kind), "version": model.version,
        "status": model.status, "algorithm": model.algorithm, "trained_at": model.trained_at,
        "training_rows": model.training_rows, "metrics": model.metrics, "notes": model.notes,
    }


def latest_model(db: Session, org_id: uuid.UUID, kind: str) -> MLModel | None:
    return db.scalar(select(MLModel).where(MLModel.organization_id == org_id, MLModel.kind == kind)
                     .order_by(MLModel.version.desc()).limit(1))


def month_end_forecast(db: Session, org_id: uuid.UUID, today: date, salesperson_ids: frozenset | None) -> dict:
    """This month's forecast for any scope, computed now with the trained method and error profile.

    Company-wide scopes use the company's back-test errors; anything narrower (a team, one person)
    uses the pooled individual errors, which are wider.
    """
    model = active_model(db, org_id, "month_end_forecast")
    if model is None:
        latest = latest_model(db, org_id, "month_end_forecast")
        return {"available": False, "reason": latest.notes if latest else "Models haven't been trained yet.",
                "model": model_summary(latest)}
    level = "company" if salesperson_ids is None else "individual"
    profile = model.params.get(level) or model.params["company"]
    series = daily_series(db, org_id, today, salesperson_ids)
    start = month_start(today)
    target = service.target_total(db, service.Filters(organization_id=org_id, start=start, end=today, salesperson_ids=salesperson_ids))
    result = forecast.forecast_month_end(series, today, profile["method"], profile["errors"], float(target) if target else None)

    cumulative, running = [], 0.0
    d = start
    while d <= today:
        running += series.get(d, 0.0)
        cumulative.append({"date": d, "cumulative": round(running, 2)})
        d = d.fromordinal(d.toordinal() + 1)
    return {
        "available": True,
        "month": start,
        "month_end": month_end(start),
        "as_of": today,
        "level": level,
        "target": float(target) if target is not None else None,
        **result,
        "tested_error_pct": profile["mape"][profile["method"]],
        "pace_error_pct": profile["mape"]["pace"],
        "daily": cumulative,
        "model": model_summary(model),
    }


def stored_predictions(db: Session, org_id: uuid.UUID, kind: str) -> tuple[MLModel | None, list[Prediction]]:
    model = active_model(db, org_id, kind)
    if model is None:
        return latest_model(db, org_id, kind), []
    return model, list(db.scalars(select(Prediction).where(Prediction.model_id == model.id)))
