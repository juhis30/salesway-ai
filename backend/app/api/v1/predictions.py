import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import service
from app.analytics.periods import org_today
from app.api.v1.analytics import get_filters
from app.core.database import get_db
from app.core.deps import get_current_org, get_current_user, require_roles
from app.ml import serving
from app.ml.recommend import KIND_ORDER
from app.ml.train import train_organization
from app.models import (
    ORG_WIDE_ROLES,
    Customer,
    Opportunity,
    Organization,
    Prediction,
    Recommendation,
    Role,
    User,
)
from app.services import audit
from app.services.access import salesperson_scope

router = APIRouter(tags=["predictions"])
can_train = require_roles(Role.SUPER_ADMIN, Role.ORG_ADMIN, Role.ANALYST)

MODEL_KINDS = ("month_end_forecast", "next_month_forecast", "churn", "deal_win")


class ModelOut(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    version: int
    status: str
    algorithm: str
    trained_at: datetime
    training_rows: int
    metrics: dict
    notes: str | None


@router.get("/ml/models", response_model=list[ModelOut])
def models(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The latest version of each model, with how it scored when tested on past data."""
    out = []
    for kind in MODEL_KINDS:
        model = serving.latest_model(db, user.organization_id, kind)
        if model:
            out.append(serving.model_summary(model))
    return out


@router.post("/ml/train")
def train(user: User = Depends(can_train), org: Organization = Depends(get_current_org), db: Session = Depends(get_db)):
    """Retrain every model on the latest data and refresh predictions and suggested actions."""
    summary = train_organization(db, org)
    audit.record(db, org.id, user.id, "ml.trained", "organization", org.id,
                 {k: v for k, v in summary.items() if isinstance(v, str)})
    db.commit()
    return summary


# --- Forecasts ----------------------------------------------------------------------------------


class DailyPoint(BaseModel):
    date: date
    cumulative: float


class MonthEndOut(BaseModel):
    available: bool
    reason: str | None = None
    month: date | None = None
    month_end: date | None = None
    as_of: date | None = None
    level: Literal["company", "individual"] | None = None
    target: float | None = None
    method: str | None = None
    method_label: str | None = None
    so_far: float | None = None
    forecast: float | None = None
    lower: float | None = None
    upper: float | None = None
    chance_of_target: float | None = None
    based_on_samples: int | None = None
    tested_error_pct: float | None = None
    pace_error_pct: float | None = None
    daily: list[DailyPoint] = []
    model: ModelOut | None = None


@router.get("/predictions/month-end", response_model=MonthEndOut)
def month_end(
    f: service.Filters = Depends(get_filters),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    """This month's expected total for the caller's scope (or a salesperson / team filter)."""
    if f.product_id or f.territory_id or f.customer_segment or f.customer_id:
        return MonthEndOut(available=False, reason="Forecasts are by salesperson or team, not by product, territory or segment.")
    return serving.month_end_forecast(db, org.id, org_today(org.timezone), f.salesperson_ids)


class NextMonthOut(BaseModel):
    available: bool
    reason: str | None = None
    month: date | None = None
    forecast: Decimal | None = None
    lower: Decimal | None = None
    upper: Decimal | None = None
    based_on_samples: int | None = None
    tested_error_pct: float | None = None
    model: ModelOut | None = None


@router.get("/predictions/next-month", response_model=NextMonthOut)
def next_month(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ORG_WIDE_ROLES:
        return NextMonthOut(available=False, reason="Next month's forecast is company-wide only.")
    model, rows = serving.stored_predictions(db, user.organization_id, "next_month_forecast")
    if not rows:
        return NextMonthOut(available=False, reason=model.notes if model else "Models haven't been trained yet.",
                            model=serving.model_summary(model))
    p = max(rows, key=lambda r: r.as_of)
    return NextMonthOut(available=True, month=p.target_period, forecast=p.value, lower=p.lower, upper=p.upper,
                        based_on_samples=(p.details or {}).get("samples"),
                        tested_error_pct=model.metrics.get("chosen_mape_pct"), model=serving.model_summary(model))


# --- Scores -------------------------------------------------------------------------------------


class ScoreRow(BaseModel):
    entity_id: uuid.UUID
    name: str
    owner_name: str | None
    probability: float
    value: Decimal | None
    signals: list[str] = []


class ScoresOut(BaseModel):
    available: bool
    reason: str | None = None
    as_of: date | None = None
    model: ModelOut | None = None
    rows: list[ScoreRow] = []


def _scores(db: Session, user: User, kind: str, entity_model, owner_column) -> ScoresOut:
    model, rows = serving.stored_predictions(db, user.organization_id, kind)
    if not rows:
        return ScoresOut(available=False, reason=model.notes if model else "Models haven't been trained yet.",
                         model=serving.model_summary(model))
    scope = salesperson_scope(db, user)
    entities = {e.id: e for e in db.scalars(select(entity_model).where(entity_model.id.in_([r.entity_id for r in rows])))}
    owners = dict(db.execute(select(User.id, User.full_name).where(User.organization_id == user.organization_id)).tuples().all())
    out = []
    for r in rows:
        entity = entities.get(r.entity_id)
        owner = getattr(entity, owner_column) if entity else None
        if entity is None or (scope is not None and owner not in scope):
            continue
        out.append(ScoreRow(entity_id=r.entity_id, name=entity.name, owner_name=owners.get(owner), probability=r.score,
                            value=r.value, signals=(r.details or {}).get("signals", [])))
    out.sort(key=lambda x: x.probability, reverse=True)
    return ScoresOut(available=True, as_of=rows[0].as_of, model=serving.model_summary(model), rows=out)


@router.get("/predictions/churn", response_model=ScoresOut)
def churn_scores(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _scores(db, user, "churn", Customer, "owner_id")


@router.get("/predictions/deals", response_model=ScoresOut)
def deal_scores(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _scores(db, user, "deal_win", Opportunity, "owner_id")


# --- Next best actions --------------------------------------------------------------------------


class RecommendationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    reason: str
    expected_value: Decimal | None
    score: float | None
    status: str
    as_of: date
    owner_id: uuid.UUID
    owner_name: str | None
    entity_type: str
    entity_id: uuid.UUID


class RecommendationUpdate(BaseModel):
    status: Literal["open", "done", "dismissed"]


@router.get("/recommendations", response_model=list[RecommendationOut])
def recommendations(
    status_filter: Literal["open", "done", "dismissed"] = "open",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Suggested actions for you (managers also see their team's)."""
    stmt = select(Recommendation).where(Recommendation.organization_id == user.organization_id,
                                        Recommendation.status == status_filter)
    scope = salesperson_scope(db, user)
    if scope is not None:
        stmt = stmt.where(Recommendation.user_id.in_(scope))
    items = db.scalars(stmt).all()
    owners = dict(db.execute(select(User.id, User.full_name).where(User.organization_id == user.organization_id)).tuples().all())
    items = sorted(items, key=lambda r: (r.user_id != user.id, KIND_ORDER.get(r.kind, 9), -(r.expected_value or 0)))
    return [RecommendationOut(id=r.id, kind=r.kind, title=r.title, reason=r.reason, expected_value=r.expected_value,
                              score=r.score, status=r.status, as_of=r.as_of, owner_id=r.user_id,
                              owner_name=owners.get(r.user_id), entity_type=r.entity_type, entity_id=r.entity_id)
            for r in items]


@router.patch("/recommendations/{recommendation_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_recommendation(recommendation_id: uuid.UUID, body: RecommendationUpdate,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = db.get(Recommendation, recommendation_id)
    if rec is None or rec.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found")
    scope = salesperson_scope(db, user)
    if scope is not None and rec.user_id not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot change this suggestion")
    rec.status = body.status
    audit.record(db, user.organization_id, user.id, "recommendation.updated", "recommendation", rec.id, {"status": body.status})
    db.commit()
