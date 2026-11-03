"""Customer health, product trends, pipeline and team activity, checked by hand.

Orders from the sales_org fixture (counted only):
  C1 Gupta Stores (ravi): SO-6 10 Sep 2000 · SO-1 5 Oct 2500 · SO-2 15 Oct 1500
  C2 Mehta Mart  (sita): SO-3 20 Oct 5000
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.analytics import insights
from app.analytics.service import Filters
from app.tests.conftest import auth_headers, upload

TODAY = date(2026, 11, 2)

OPPORTUNITIES = (
    "opportunity_id,deal_name,customer_code,owner_email,stage,deal_value,created_date,close_date,closed_date,lost_reason\n"
    "OPP-1,Big deal,C1,ravi@acme.in,proposal,100000,2026-09-01,2026-10-01,,\n"
    "OPP-2,Small deal,C2,sita@acme.in,Negotiation/Review,40000,2026-10-20,2026-12-01,,\n"
    "OPP-3,Won deal,C1,ravi@acme.in,Closed Won,50000,2026-08-01,,2026-09-10,\n"
    "OPP-4,Lost deal,C2,sita@acme.in,closed lost,30000,2026-09-01,,2026-10-05,Price too high\n"
    "OPP-5,Bad deal,C2,sita@acme.in,won,10,2026-09-01,,,\n"  # won without a close date
)
ACTIVITIES = (
    "date,salesperson_email,type,customer,opportunity_id,status,notes\n"
    "2026-10-25,sita@acme.in,meeting,,OPP-2,done,Pricing\n"          # customer comes from the deal
    "2026-10-03,ravi@acme.in,visit,C1,,done,\n"                      # SO-1 on 5 Oct -> converted
    "2026-10-20,ravi@acme.in,visit,C1,,done,\n"                      # no order within 7 days
    "2026-10-10,ravi@acme.in,call,C1,,done,\n"
    "2026-10-28,ravi@acme.in,Phone Call,C1,,planned,Follow up\n"     # overdue on 2 Nov
    "2026-11-10,sita@acme.in,visit,C2,,scheduled,\n"                 # upcoming
    "2026-10-03,ravi@acme.in,visit,C1,,done,\n"                      # duplicate row
)


@pytest.fixture
def pipeline_org(client, sales_org):
    admin = sales_org["admin"]
    opps = upload(client, admin, "opportunities", OPPORTUNITIES, confirm=False)
    assert opps["job"]["valid_rows"] == 4
    assert [(e["row"], e["field"]) for e in opps["errors"]] == [(6, "closed_on")]
    client.post(f"/api/v1/imports/{opps['job']['id']}/confirm", headers=admin)

    acts = upload(client, admin, "activities", ACTIVITIES, confirm=False)
    assert acts["job"]["valid_rows"] == 6
    assert [e["row"] for e in acts["errors"]] == [8]
    client.post(f"/api/v1/imports/{acts['job']['id']}/confirm", headers=admin)

    org_id = uuid.UUID(client.get("/api/v1/auth/me", headers=admin).json()["organization"]["id"])
    return {**sales_org, "org_id": org_id}


def test_customer_health_over_time(client, sales_org):
    admin = sales_org["admin"]

    def health(start, end):
        return client.get(f"/api/v1/analytics/customer-health?start={start}&end={end}", headers=admin).json()

    october = health("2026-10-01", "2026-10-31")
    s = october["summary"]
    # C1 gaps 25 and 10 days -> typical 17.5; 16 days since last order -> active.
    # C2 has one order, so it uses the median typical gap (17.5); 11 days -> active.
    assert (s["active"], s["at_risk"], s["dormant"]) == (2, 0, 0)
    assert (s["new_in_period"], s["returning_in_period"], s["repeat_rate_pct"]) == (1, 1, 50.0)
    c1 = next(c for c in october["customers"] if c["code"] == "C1")
    assert (c1["typical_gap_days"], c1["days_since_last_order"], c1["monthly_value"]) == (18, 16, "1000.00")

    # By 30 Nov both are past 1.5 x 17.5 = 26.25 days -> at risk; value at stake 1000 + 833.33.
    november = health("2026-11-01", "2026-11-30")
    assert (november["summary"]["at_risk"], november["summary"]["monthly_value_at_risk"]) == (2, "1833.33")
    assert november["customers"][0]["reason"].startswith("No order for 46 days; usually orders every 18 days")

    # By 31 Dec both are 60+ days quiet -> dormant.
    assert health("2026-12-01", "2026-12-31")["summary"]["dormant"] == 2

    only = client.get("/api/v1/analytics/customer-health?start=2026-11-01&end=2026-11-30&status=active", headers=admin).json()
    assert only["customers"] == [] and only["summary"]["at_risk"] == 2


def test_product_trends(client, sales_org):
    r = client.get("/api/v1/analytics/product-trends?start=2026-10-01&end=2026-10-31", headers=sales_org["admin"]).json()
    assert r["comparison_available"] is True
    rows = {p["sku"]: p for p in r["products"]}
    assert (rows["P1"]["revenue"], rows["P1"]["previous_revenue"], rows["P1"]["growth_pct"], rows["P1"]["trend"]) == ("7000.00", "2000.00", 250.0, "rising")
    assert (rows["P2"]["previous_revenue"], rows["P2"]["trend"], rows["P2"]["buyers"]) == ("0", "new", 1)
    assert r["counts"] == {"rising": 1, "new": 1}

    # September's comparison (August) predates the data: no trend labels at all.
    sept = client.get("/api/v1/analytics/product-trends?start=2026-09-01&end=2026-09-30", headers=sales_org["admin"]).json()
    assert sept["comparison_available"] is False
    assert {p["trend"] for p in sept["products"]} == {None}

    # A period shorter than two weeks is too noisy to label, even with comparison data.
    short = client.get("/api/v1/analytics/product-trends?start=2026-10-01&end=2026-10-10", headers=sales_org["admin"]).json()
    assert (short["comparison_available"], short["trends_available"]) == (True, False)
    assert {p["trend"] for p in short["products"]} == {None} and short["counts"] == {}
    assert short["products"][0]["growth_pct"] is not None  # the raw change is still shown


def test_pipeline(db_session, pipeline_org):
    f = Filters(organization_id=pipeline_org["org_id"], start=date(2026, 9, 1), end=date(2026, 10, 31))
    p = insights.pipeline(db_session, f, TODAY)
    assert (p["open_count"], p["open_value"], p["weighted_value"]) == (2, Decimal("140000"), Decimal("80000.00"))  # 50% + 75%
    assert (p["won_count"], p["lost_count"], p["win_rate_pct"]) == (1, 1, 50.0)
    assert (p["average_won_deal"], p["average_cycle_days"]) == (Decimal("50000.00"), 40)

    deals = {d["code"]: d for d in p["deals"]}
    # OPP-1: no activity since it opened on 1 Sep (62 days) and its close date has passed.
    assert (deals["OPP-1"]["stalled"], deals["OPP-1"]["overdue"], deals["OPP-1"]["days_idle"]) == (True, True, 62)
    assert (deals["OPP-2"]["stalled"], deals["OPP-2"]["last_activity_date"]) == (False, date(2026, 10, 25))
    assert (p["stalled_count"], p["overdue_count"]) == (1, 1)


def test_team_activity(db_session, pipeline_org):
    f = Filters(organization_id=pipeline_org["org_id"], start=date(2026, 10, 1), end=date(2026, 10, 31))
    people = {r["name"]: r for r in insights.team_activity(db_session, f, TODAY)["people"]}
    ravi, sita = people["Ravi"], people["Sita"]
    assert (ravi["visits"], ravi["calls"], ravi["customers_contacted"]) == (2, 1, 1)
    assert (ravi["visit_to_order_pct"], ravi["revenue_per_visit"]) == (50.0, Decimal("2000.00"))
    assert (ravi["follow_ups_overdue"], ravi["orders"], ravi["revenue"]) == (1, 2, Decimal("4000.00"))
    assert (sita["meetings"], sita["visits"], sita["visit_to_order_pct"], sita["follow_ups_upcoming"]) == (1, 0, None, 1)
    assert "Admin User" not in people  # no field activity


def test_follow_ups_and_marking_done(client, pipeline_org):
    ravi = auth_headers(client, "ravi@acme.in")
    sita = auth_headers(client, "sita@acme.in")
    overdue = client.get("/api/v1/activities/follow-ups?when=overdue", headers=ravi).json()
    assert [(a["customer_name"], a["activity_type"], a["notes"]) for a in overdue] == [("Gupta Stores", "call", "Follow up")]
    assert client.get("/api/v1/activities/follow-ups?when=overdue", headers=sita).json() == []

    activity_id = overdue[0]["id"]
    assert client.patch(f"/api/v1/activities/{activity_id}", headers=sita, json={"status": "done"}).status_code == 403
    assert client.patch(f"/api/v1/activities/{activity_id}", headers=ravi, json={"status": "done"}).status_code == 204
    assert client.get("/api/v1/activities/follow-ups?when=overdue", headers=ravi).json() == []


def test_insights_respect_scope(client, pipeline_org):
    ravi = auth_headers(client, "ravi@acme.in")
    health = client.get("/api/v1/analytics/customer-health?start=2026-10-01&end=2026-10-31", headers=ravi).json()
    assert [c["code"] for c in health["customers"]] == ["C1"]
    pipe = client.get("/api/v1/analytics/pipeline?start=2026-09-01&end=2026-10-31", headers=ravi).json()
    assert [d["code"] for d in pipe["deals"]] == ["OPP-1"]
    team = client.get("/api/v1/analytics/team-activity?start=2026-10-01&end=2026-10-31", headers=ravi).json()
    assert [p["name"] for p in team["people"]] == ["Ravi"]


def test_reimporting_activities_skips_duplicates(client, pipeline_org):
    again = upload(client, pipeline_org["admin"], "activities", ACTIVITIES)
    assert again["result"] == {"created": 0, "updated": 0, "skipped_existing": 6}
