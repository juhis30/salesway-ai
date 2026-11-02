"""Predictive models: forecast maths, the accuracy gates, actions, and a full run on the demo company."""

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ml import churn, deals, forecast, recommend
from app.models import Prediction, Recommendation, User
from app.tests.conftest import PASSWORD, auth_headers, register_org

TODAY = date(2026, 11, 2)


# --- Forecast maths --------------------------------------------------------------------------


def weekly_series(start: date, end: date) -> dict[date, float]:
    """100 a day Monday-Friday, 50 on Saturday, nothing on Sunday."""
    out, d = {}, start
    while d <= end:
        out[d] = {5: 50.0, 6: 0.0}.get(d.weekday(), 100.0)
        d += timedelta(days=1)
    return out


def test_methods_on_a_regular_week():
    series = weekly_series(date(2026, 1, 1), date(2026, 10, 31))
    cut = date(2026, 10, 14)
    actual = sum(v for d, v in series.items() if d.month == 10)
    assert forecast.weekday_profile(series, cut) == pytest.approx(actual)  # a regular week is predicted exactly
    assert forecast.pace(series, cut) == pytest.approx(sum(series[date(2026, 10, d)] for d in range(1, 15)) / 14 * 31)


def test_backtest_picks_the_method_that_was_most_accurate():
    series = weekly_series(date(2026, 1, 1), date(2026, 10, 31))
    bt = forecast.backtest_month_end(series, TODAY)
    assert bt["months"] == 8  # March-October: complete months after the 8-week warm-up
    assert bt["samples"] == 8 * len(forecast.CUT_DAYS)
    assert forecast.choose(bt) == "weekday_profile"
    assert bt["mape"]["weekday_profile"] == 0.0


def test_too_little_history_means_no_forecast():
    series = weekly_series(date(2026, 8, 1), date(2026, 10, 31))
    assert forecast.choose(forecast.backtest_month_end(series, TODAY)) is None


def test_range_and_chance_come_from_past_errors():
    series = {date(2026, 11, 1): 0.0, date(2026, 11, 2): 1000.0}
    series.update({date(2026, 10, 1) + timedelta(days=i): 1000.0 for i in range(31)})
    # Past forecasts at this stage ran 25% high, exactly right, or 20% low.
    errors = {"early": [0.25, 0.0, -0.2] * 3, "mid": [], "late": []}
    result = forecast.forecast_month_end(series, TODAY, "recent_run_rate", errors, target=31000)
    # so far (1,000) + 28 remaining days at the last 28 days' rate: 26 Oct days + Sunday 1 Nov (0) + 2 Nov.
    point = 1000 + 27000 / 28 * 28
    assert result["forecast"] == pytest.approx(point)
    outcomes = sorted([point / 1.25, point, point / 0.8] * 3)
    assert result["lower"] == pytest.approx(forecast._quantile(outcomes, 0.1))
    assert result["upper"] == pytest.approx(forecast._quantile(outcomes, 0.9))
    assert result["chance_of_target"] == pytest.approx(1 / 3)  # only the "ran 20% low" outcomes reach 31,000


# --- Churn ---------------------------------------------------------------------------------------


def orders_every(days: int, start: date, end: date) -> list[churn.Order]:
    out, d = [], start
    while d <= end:
        out.append(churn.Order(d, 1000.0, frozenset({"p"})))
        d += timedelta(days=days)
    return out


def test_churn_labels_use_only_the_past_and_the_next_60_days():
    loyal, leaver = uuid.uuid4(), uuid.uuid4()
    history = {
        loyal: orders_every(7, date(2025, 11, 1), date(2026, 11, 1)),
        leaver: orders_every(7, date(2025, 11, 1), date(2026, 5, 1)),
    }
    rows = churn.build_snapshots(history, {}, TODAY)
    by = {(r["customer"], r["cutoff"]): r for r in rows}
    cutoff = date(2026, 4, 25)  # a snapshot date in the generated schedule
    cutoff = min((c for (_, c) in by), key=lambda c: abs((c - cutoff).days))
    assert by[(loyal, cutoff)]["churned"] == 0
    assert by[(leaver, cutoff)]["churned"] == 1  # stops within 60 days of this cut-off
    # Once 60+ days quiet, the leaver is already lost and no longer counted as "at risk".
    assert not any(r["customer"] == leaver and r["cutoff"] > date(2026, 7, 1) for r in rows)


def test_churn_model_must_beat_the_rule():
    rng = random.Random(1)
    rows = []
    for i in range(400):
        churned = int(i % 10 == 0)
        gap_ratio = 3.0 + rng.random() if churned else rng.random()  # the rule separates perfectly
        rows.append({"customer": uuid.uuid4(), "segment": "Kirana", "churned": churned, "gap_ratio": gap_ratio,
                     "days_since_last": 10, "orders_90d": 5, "log_revenue_90d": 8.0, "revenue_trend": 1.0,
                     "tenure_days": 300, "products_90d": 4})
    result = churn.train(rows)
    assert result["evaluation"]["rule"]["auc"] == 1.0
    assert result["status"] == "rejected"  # a model can't be "clearly better" than a perfect rule

    few = churn.train(rows[:50])
    assert few["status"] == "insufficient_data"


# --- Deals ---------------------------------------------------------------------------------------


def make_deals(n: int, rng: random.Random) -> list[deals.Deal]:
    out = []
    for i in range(n):
        opened = date(2026, 1, 1) + timedelta(days=i)
        out.append(deals.Deal(uuid.uuid4(), uuid.uuid4(), rng.uniform(1e5, 1e6), "Referral", "Kirana", False,
                              opened, opened + timedelta(days=45), opened + timedelta(days=30), rng.random() < 0.4))
    return out


def test_deal_model_gate():
    rng = random.Random(3)
    assert deals.train(make_deals(20, rng))["status"] == "insufficient_data"
    noise = deals.train(make_deals(150, rng))  # outcomes unrelated to anything
    assert noise["status"] == "rejected"
    assert noise["evaluation"]["logistic_regression"]["auc"] < deals.MIN_AUC


# --- Next best actions ---------------------------------------------------------------------------


def action(user, kind, value):
    return recommend.Action(user, kind, "customer", uuid.uuid4(), kind, "", Decimal(value))


def test_actions_are_ordered_by_kind_then_value_and_capped():
    me = uuid.uuid4()
    actions = ([action(me, "cross_sell", 9_00_000)] + [action(me, "follow_up", v) for v in (10, 30, 20, 40)]
               + [action(me, "revive_deal", 5)] + [action(me, "win_back", 1)])
    ranked = recommend.rank(actions, snoozed=set())
    assert [(a.kind, int(a.expected_value)) for a in ranked] == [
        ("follow_up", 40), ("follow_up", 30), ("follow_up", 20),  # max 3 of a kind
        ("win_back", 1), ("revive_deal", 5), ("cross_sell", 900000),
    ]
    snoozed = {("cross_sell", actions[0].entity_id)}
    assert all(a.kind != "cross_sell" for a in recommend.rank(actions, snoozed))


def test_cross_sell_needs_most_similar_customers_to_buy_it():
    owner, atta, oil = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    day = TODAY - timedelta(days=10)
    customers, recent = {}, {}
    for i in range(6):
        cid = uuid.uuid4()
        customers[cid] = {"name": f"Store {i}", "segment": "Kirana", "owner": owner}
        recent[cid] = [(day, oil, 1000.0)] + ([(day, atta, 3000.0)] if i < 5 else [])
    out = recommend.cross_sell(recent, customers, {atta: "Atta", oil: "Oil"}, TODAY)
    assert len(out) == 1
    assert out[0].title == "Offer Atta to Store 5"
    assert "5 of 6 similar Kirana customers buy it" in out[0].reason


# --- End to end ----------------------------------------------------------------------------------


def test_small_organisations_get_honest_answers(client, sales_org):
    admin = sales_org["admin"]
    summary = client.post("/api/v1/ml/train", headers=admin).json()
    assert summary["month_end_forecast"] == "insufficient_data"
    assert summary["churn"] == "insufficient_data"
    assert summary["deal_win"] == "insufficient_data"
    forecast_out = client.get("/api/v1/predictions/month-end", headers=admin).json()
    assert forecast_out["available"] is False and "complete months" in forecast_out["reason"]
    assert client.get("/api/v1/predictions/churn", headers=admin).json()["available"] is False
    ravi = auth_headers(client, "ravi@acme.in")
    assert client.post("/api/v1/ml/train", headers=ravi).status_code == 403


@pytest.fixture
def demo(client, db_session):
    from app.seed import populate

    org, summary = populate(db_session, TODAY)
    return {"org": org, "summary": summary, "head": auth_headers(client, "vikram@shakti-demo.in", "Demo@12345"),
            "amit": auth_headers(client, "amit@shakti-demo.in", "Demo@12345")}


def test_demo_company_end_to_end(client, db_session, demo):
    s = demo["summary"]
    assert s["month_end_forecast"] == "active"
    assert s["churn"] in ("active", "rejected") and s["deal_win"] in ("active", "rejected")

    head = client.get("/api/v1/predictions/month-end", headers=demo["head"]).json()
    assert head["available"] and head["level"] == "company"
    assert head["lower"] <= head["forecast"] <= head["upper"]
    assert head["so_far"] <= head["lower"]
    assert 0 <= head["chance_of_target"] <= 1
    assert head["tested_error_pct"] <= head["pace_error_pct"]  # chosen method beat the plain pace when tested

    amit = client.get("/api/v1/predictions/month-end", headers=demo["amit"]).json()
    assert amit["level"] == "individual" and amit["forecast"] < head["forecast"]

    # Actions are scoped: Amit sees only his own; the head sees everyone's.
    amit_user = db_session.scalar(select(User).where(User.email == "amit@shakti-demo.in"))
    mine = client.get("/api/v1/recommendations", headers=demo["amit"]).json()
    assert mine and {r["owner_id"] for r in mine} == {str(amit_user.id)}
    everyone = client.get("/api/v1/recommendations", headers=demo["head"]).json()
    assert len({r["owner_id"] for r in everyone}) > 1

    # Done actions aren't suggested again on the next training run.
    done = mine[0]
    assert client.patch(f"/api/v1/recommendations/{done['id']}", headers=demo["amit"], json={"status": "done"}).status_code == 204
    client.post("/api/v1/ml/train", headers=auth_headers(client, "ananya@shakti-demo.in", "Demo@12345"))
    again = client.get("/api/v1/recommendations", headers=demo["amit"]).json()
    assert done["entity_id"] not in {r["entity_id"] for r in again if r["kind"] == done["kind"]}

    # Retraining makes a new version and retires the old one; predictions point at the active model.
    models = {m["kind"]: m for m in client.get("/api/v1/ml/models", headers=demo["head"]).json()}
    assert models["month_end_forecast"]["version"] == 2
    assert db_session.scalar(select(Prediction).where(Prediction.kind == "month_end_forecast")) is not None


def test_other_people_cannot_change_my_actions(client, db_session, demo):
    amit_user = db_session.scalar(select(User).where(User.email == "amit@shakti-demo.in"))
    rec = db_session.scalar(select(Recommendation).where(Recommendation.user_id == amit_user.id))
    rohan = auth_headers(client, "rohan@shakti-demo.in", "Demo@12345")
    assert client.patch(f"/api/v1/recommendations/{rec.id}", headers=rohan, json={"status": "done"}).status_code == 403
    other = register_org(client, "Rival Co", "admin@rival.in")
    assert client.patch(f"/api/v1/recommendations/{rec.id}", headers=other, json={"status": "done"}).status_code == 404
    assert client.get("/api/v1/recommendations", headers=other).json() == []
