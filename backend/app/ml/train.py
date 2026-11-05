"""Train every model for an organisation, store the results and refresh predictions and actions.

    python -m app.ml.train            # all organisations
"""

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.analytics import insights, service
from app.analytics.metrics import REVENUE_STATUSES
from app.analytics.periods import add_months, month_start, org_today
from app.ml import churn, deals, forecast, recommend
from app.models import (
    Customer,
    MLModel,
    Opportunity,
    OpportunityStage,
    Organization,
    Prediction,
    Product,
    Recommendation,
    SalesOrder,
    SalesOrderItem,
    User,
)

ALL_TIME = date(1900, 1, 1)


def daily_series(db: Session, org_id: uuid.UUID, today: date, salesperson_ids=None) -> forecast.Series:
    f = service.Filters(organization_id=org_id, start=ALL_TIME, end=today,
                        salesperson_ids=None if salesperson_ids is None else frozenset(salesperson_ids))
    return {p["period"]: float(p["revenue"]) for p in service.trend(db, f, "day")}


def _new_model(db: Session, org_id: uuid.UUID, kind: str, **fields) -> MLModel:
    version = (db.scalar(select(func.max(MLModel.version)).where(MLModel.organization_id == org_id, MLModel.kind == kind)) or 0) + 1
    if fields.get("status") == "active":
        for old in db.scalars(select(MLModel).where(MLModel.organization_id == org_id, MLModel.kind == kind, MLModel.status == "active")):
            old.status = "retired"
    model = MLModel(organization_id=org_id, kind=kind, version=version, trained_at=datetime.now(timezone.utc), **fields)
    db.add(model)
    db.flush()
    return model


def active_model(db: Session, org_id: uuid.UUID, kind: str) -> MLModel | None:
    return db.scalar(select(MLModel).where(MLModel.organization_id == org_id, MLModel.kind == kind, MLModel.status == "active")
                     .order_by(MLModel.version.desc()).limit(1))


# --- forecasts ----------------------------------------------------------------------------------


def _train_forecasts(db: Session, org: Organization, today: date, salespeople: list[User]) -> dict:
    company = daily_series(db, org.id, today)
    company_bt = forecast.backtest_month_end(company, today)
    company_method = forecast.choose(company_bt)

    pooled = {m: {"early": [], "mid": [], "late": []} for m in forecast.MONTH_END_METHODS}
    individual_series = {}
    for person in salespeople:
        series = daily_series(db, org.id, today, [person.id])
        if not series:
            continue
        individual_series[person.id] = series
        bt = forecast.backtest_month_end(series, today)
        for m, stages in bt.get("errors", {}).items():
            for stage, errs in stages.items():
                pooled[m][stage] += errs
    pooled_bt = {"samples": sum(len(v) for v in pooled["pace"].values()), "errors": pooled,
                 "mape": {m: forecast._mape([e for s in st.values() for e in s]) for m, st in pooled.items()}}
    individual_method = forecast.choose(pooled_bt)

    if company_method is None:
        model = _new_model(db, org.id, "month_end_forecast", algorithm="backtest-selected", status="insufficient_data",
                           training_rows=company_bt.get("samples", 0), metrics={},
                           params={}, notes="Needs at least 3 complete months of sales after an 8-week warm-up.")
        return {"month_end_forecast": model.status}

    def profile(bt, method):
        return {"method": method, "errors": bt["errors"][method], "mape": bt["mape"], "samples": bt["samples"]}

    model = _new_model(
        db, org.id, "month_end_forecast",
        algorithm=company_method, status="active",
        data_start=min(company), data_end=today, training_rows=company_bt["samples"],
        metrics={"mape_pct": company_bt["mape"], "chosen_mape_pct": company_bt["mape"][company_method],
                 "baseline_mape_pct": company_bt["mape"]["pace"], "months_tested": company_bt["months"],
                 "individual_mape_pct": pooled_bt["mape"]},
        params={"company": profile(company_bt, company_method),
                "individual": profile(pooled_bt, individual_method) if individual_method else None},
        notes=f"Chosen by back-testing {company_bt['samples']} forecasts over {company_bt['months']} months.",
    )

    # Store this month's forecasts (company and each salesperson) for later accuracy tracking.
    month = month_start(today)
    target_f = service.Filters(organization_id=org.id, start=month, end=today)
    company_target = service.target_total(db, target_f)
    stored = [("organization", org.id, company, model.params["company"], company_target)]
    if individual_method:
        for person_id, series in individual_series.items():
            stored.append(("salesperson", person_id, series, model.params["individual"],
                           service.target_total(db, service.Filters(organization_id=org.id, start=month, end=today, salesperson_ids=frozenset({person_id})))))
    for entity_type, entity_id, series, prof, target in stored:
        result = forecast.forecast_month_end(series, today, prof["method"], prof["errors"], float(target) if target else None)
        db.add(Prediction(organization_id=org.id, model_id=model.id, kind="month_end_forecast", entity_type=entity_type,
                          entity_id=entity_id, as_of=today, target_period=month, score=result["chance_of_target"],
                          value=_money(result["forecast"]), lower=_money(result["lower"]), upper=_money(result["upper"]),
                          details={"method": result["method"], "so_far": round(result["so_far"], 2),
                                   "target": float(target) if target else None, "samples": result["based_on_samples"]}))

    totals = [t for _, t in forecast.monthly_totals(company, today)]
    nm_bt = forecast.backtest_next_month(totals)
    if nm_bt["samples"] >= 4:
        nm_method = min(nm_bt["mape"], key=lambda m: nm_bt["mape"][m])
        nm = _new_model(db, org.id, "next_month_forecast", algorithm=nm_method, status="active",
                        data_start=min(company), data_end=today, training_rows=nm_bt["samples"],
                        metrics={"mape_pct": nm_bt["mape"], "chosen_mape_pct": nm_bt["mape"][nm_method]},
                        params={"method": nm_method, "errors": nm_bt["errors"][nm_method]},
                        notes=f"Only {nm_bt['samples']} past months to test on: treat as a rough guide.")
        result = forecast.forecast_next_month(totals, nm_method, nm_bt["errors"][nm_method])
        db.add(Prediction(organization_id=org.id, model_id=nm.id, kind="next_month_forecast", entity_type="organization",
                          entity_id=org.id, as_of=today, target_period=add_months(month, 1), value=_money(result["forecast"]),
                          lower=_money(result["lower"]), upper=_money(result["upper"]),
                          details={"method": nm_method, "samples": result["based_on_samples"]}))
    else:
        _new_model(db, org.id, "next_month_forecast", algorithm="-", status="insufficient_data", training_rows=nm_bt["samples"],
                   metrics={}, params={}, notes="Needs at least 10 complete months of sales.")
    return {"month_end_forecast": "active", "month_end_method": company_method}


def _money(value) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 2)))


# --- churn --------------------------------------------------------------------------------------


def _order_history(db: Session, org_id: uuid.UUID, today: date):
    rows = db.execute(
        select(SalesOrder.customer_id, SalesOrder.id, SalesOrder.order_date, SalesOrderItem.product_id, SalesOrderItem.line_amount)
        .join(SalesOrderItem, SalesOrderItem.order_id == SalesOrder.id)
        .where(SalesOrder.organization_id == org_id, SalesOrder.status.in_(REVENUE_STATUSES), SalesOrder.order_date <= today)
    ).all()
    return churn.group_orders(rows)


def _monthly_value(orders: list[churn.Order]) -> float:
    last = max(o.day for o in orders)
    return round(sum(o.revenue for o in orders if o.day > last - timedelta(days=182)) / 6, 2)


def _train_churn(db: Session, org: Organization, today: date, history, customers: dict) -> tuple[dict, dict]:
    segments = {cid: c["segment"] for cid, c in customers.items()}
    rows = churn.build_snapshots(history, segments, today)
    result = churn.train(rows)
    model = _new_model(
        db, org.id, "churn", algorithm=result.get("algorithm", "-"), status=result["status"],
        data_start=min((r["cutoff"] for r in rows), default=None), data_end=today, training_rows=result["rows"],
        metrics={k: result[k] for k in ("evaluation", "positives", "customers") if k in result},
        params=result.get("params", {}), notes=result.get("reason"),
    )

    fallback = churn._fallback_gap(history, today)
    current = []
    for cid, orders in history.items():
        feats = churn.features_at(orders, today, fallback)
        if feats is not None and cid in customers:
            current.append({"customer": cid, "segment": segments.get(cid) or "Unknown", **feats})
    probabilities = churn.score(result["model"], result["segment_names"], current) if result["status"] == "active" else [None] * len(current)

    scored = {}
    for row, p in zip(current, probabilities):
        value = _monthly_value(history[row["customer"]])
        signals = churn.signals(row)
        scored[row["customer"]] = {"probability": p, "signals": signals, "monthly_value": value, "features": row}
        if p is not None:
            db.add(Prediction(organization_id=org.id, model_id=model.id, kind="churn", entity_type="customer",
                              entity_id=row["customer"], as_of=today, score=round(p, 4), value=_money(value),
                              details={"signals": signals, "days_since_last": row["days_since_last"],
                                       "typical_gap": round(row["typical_gap"], 1)}))
    return {"churn": result["status"], "churn_auc": result.get("evaluation", {})}, scored


# --- deals --------------------------------------------------------------------------------------


def _train_deals(db: Session, org: Organization, today: date, history, customers: dict) -> tuple[dict, dict]:
    first_order = {cid: min(o.day for o in orders) for cid, orders in history.items()}
    all_deals = []
    for o in db.scalars(select(Opportunity).where(Opportunity.organization_id == org.id)):
        c = customers.get(o.customer_id, {})
        all_deals.append(deals.Deal(
            id=o.id, owner=o.owner_id, amount=float(o.amount), source=o.source or "Unknown",
            segment=c.get("segment") or "Unknown",
            existing_customer=first_order.get(o.customer_id, date.max) < o.opened_on,
            opened_on=o.opened_on, expected_close=o.expected_close, closed_on=o.closed_on,
            won=True if o.stage == OpportunityStage.WON else False if o.stage == OpportunityStage.LOST else None,
        ))
    closed = [d for d in all_deals if d.won is not None and d.closed_on]
    open_ = [d for d in all_deals if d.won is None]
    result = deals.train(closed)
    model = _new_model(db, org.id, "deal_win", algorithm=result.get("algorithm", "-"), status=result["status"],
                       data_end=today, training_rows=result["rows"],
                       metrics={k: result[k] for k in ("evaluation", "won") if k in result}, params={},
                       notes=result.get("reason"))
    probabilities = {}
    if result["status"] == "active":
        for d, p in zip(open_, deals.score(result, open_, closed, today)):
            probabilities[d.id] = p
            db.add(Prediction(organization_id=org.id, model_id=model.id, kind="deal_win", entity_type="opportunity",
                              entity_id=d.id, as_of=today, score=round(p, 4), value=_money(d.amount)))
    return {"deal_win": result["status"], "deal_auc": result.get("evaluation", {})}, probabilities


# --- recommendations ----------------------------------------------------------------------------


def _refresh_recommendations(db: Session, org: Organization, today: date, history, customers, churn_scores, win_probs) -> int:
    f = service.Filters(organization_id=org.id, start=month_start(today), end=today)
    at_risk = []
    for cid, s in churn_scores.items():
        c = customers[cid]
        rule_flag = s["features"]["gap_ratio"] >= insights.AT_RISK_GAP_MULTIPLIER and s["features"]["days_since_last"] >= insights.AT_RISK_MIN_DAYS
        if c["owner"] and (s["probability"] is not None or rule_flag):
            at_risk.append({"owner": c["owner"], "id": cid, "name": c["name"], "monthly_value": s["monthly_value"],
                            "churn_probability": s["probability"], "signals": s["signals"]})
    monthly = {cid: _monthly_value(orders) for cid, orders in history.items()}

    overdue = insights.follow_ups(db, f, today, "overdue", limit=500)
    for item in overdue:
        item["user_id"] = item["salesperson_id"]
    pipeline = insights.pipeline(db, f, today)
    owners = dict(db.execute(select(Opportunity.id, Opportunity.owner_id).where(Opportunity.organization_id == org.id)).tuples().all())
    for d in pipeline["deals"]:
        d["owner_id"] = owners.get(d["opportunity_id"])

    recent: dict[uuid.UUID, list] = defaultdict(list)
    for cid, orders in history.items():
        for o in orders:
            if o.day > today - timedelta(days=180):
                for p in o.products:
                    recent[cid].append((o.day, p, o.revenue / max(len(o.products), 1)))
    products = dict(db.execute(select(Product.id, Product.name).where(Product.organization_id == org.id)).tuples().all())

    actions = (recommend.win_back(at_risk)
               + recommend.follow_ups([a for a in overdue if a["user_id"]], monthly)
               + recommend.revive_deals(pipeline["deals"], win_probs, insights.STAGE_WEIGHTS)
               + recommend.cross_sell(recent, customers, products, today))

    snoozed = set(db.execute(select(Recommendation.kind, Recommendation.entity_id).where(
        Recommendation.organization_id == org.id, Recommendation.status.in_(("done", "dismissed")),
        Recommendation.updated_at >= datetime.now(timezone.utc) - timedelta(days=recommend.SNOOZE_DAYS))).tuples().all())
    db.execute(delete(Recommendation).where(Recommendation.organization_id == org.id, Recommendation.status == "open"))
    chosen = recommend.rank(actions, snoozed)
    for a in chosen:
        db.add(Recommendation(organization_id=org.id, user_id=a.user_id, kind=a.kind, entity_type=a.entity_type,
                              entity_id=a.entity_id, title=a.title, reason=a.reason, expected_value=a.expected_value,
                              score=a.score, status="open", as_of=today, details=a.details))
    return len(chosen)


# --- entry point --------------------------------------------------------------------------------


def train_organization(db: Session, org: Organization, today: date | None = None) -> dict:
    today = today or org_today(org.timezone)
    salespeople = list(db.scalars(select(User).where(User.organization_id == org.id, User.deleted_at.is_(None),
                                                     User.id.in_(select(SalesOrder.salesperson_id).where(SalesOrder.organization_id == org.id)))))
    customers = {c.id: {"name": c.name, "segment": c.segment, "owner": c.owner_id}
                 for c in db.scalars(select(Customer).where(Customer.organization_id == org.id))}
    history = _order_history(db, org.id, today)

    summary = {"as_of": today.isoformat()}
    summary.update(_train_forecasts(db, org, today, salespeople))
    churn_summary, churn_scores = _train_churn(db, org, today, history, customers)
    summary.update(churn_summary)
    deal_summary, win_probs = _train_deals(db, org, today, history, customers)
    summary.update(deal_summary)
    summary["recommendations"] = _refresh_recommendations(db, org, today, history, customers, churn_scores, win_probs)
    db.flush()
    return summary


if __name__ == "__main__":
    import json

    from app.core.database import system_session

    session = system_session()
    try:
        for organization in session.scalars(select(Organization)):
            result = train_organization(session, organization)
            session.commit()
            print(organization.name, json.dumps(result, indent=1, default=str))
    finally:
        session.close()
