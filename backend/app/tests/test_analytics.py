from datetime import date
from decimal import Decimal

from app.analytics import metrics
from app.analytics.periods import add_months, previous_period
from app.tests.conftest import auth_headers

OCT = "start=2026-10-01&end=2026-10-31"


def test_overview_matches_hand_calculation(client, sales_org):
    o = client.get(f"/api/v1/analytics/overview?{OCT}", headers=sales_org["admin"]).json()
    assert o["revenue"] == "9000.00"          # cancelled and pending excluded; tax excluded
    assert o["orders"] == 3
    assert o["average_order_value"] == "3000.00"
    assert o["active_customers"] == 2
    assert o["target"] == "13000.00"
    assert o["achievement_pct"] == 69.2       # 9000 / 13000
    assert o["target_gap"] == "4000.00"
    assert o["comparison_period"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert o["comparison_available"] is True
    assert o["previous_revenue"] == "2000.00"
    assert o["revenue_growth_pct"] == 350.0   # (9000 - 2000) / 2000
    assert o["orders_growth_pct"] == 200.0    # 3 orders vs 1
    assert o["data_as_of"] == "2026-10-22"


def test_no_growth_when_comparison_predates_data(client, sales_org):
    # Sep-Oct compares with 2 Jul - 31 Aug, but the data only starts in September.
    o = client.get("/api/v1/analytics/overview?start=2026-09-01&end=2026-10-31", headers=sales_org["admin"]).json()
    assert o["comparison_available"] is False
    assert o["revenue_growth_pct"] is None
    assert o["orders_growth_pct"] is None
    assert o["required_daily_run_rate"] is None  # multi-month period


def test_filters(client, sales_org):
    admin = sales_org["admin"]
    sita = sales_org["sita"]["id"]
    o = client.get(f"/api/v1/analytics/overview?{OCT}&salesperson_id={sita}", headers=admin).json()
    assert (o["revenue"], o["target"], o["achievement_pct"]) == ("5000.00", "5000.00", 100.0)

    o = client.get(f"/api/v1/analytics/overview?{OCT}&customer_segment=Kirana", headers=admin).json()
    assert o["revenue"] == "4000.00"
    assert o["target"] is None  # targets don't split by segment

    product = next(p for p in client.get("/api/v1/products", headers=admin).json()["items"] if p["sku"] == "P2")
    o = client.get(f"/api/v1/analytics/overview?{OCT}&product_id={product['id']}", headers=admin).json()
    assert (o["revenue"], o["orders"]) == ("2000.00", 2)


def test_leaderboard(client, sales_org):
    board = client.get(f"/api/v1/analytics/leaderboard?{OCT}", headers=sales_org["admin"]).json()
    assert [(r["rank"], r["name"], r["revenue"], r["achievement_pct"]) for r in board] == [
        (1, "Sita", "5000.00", 100.0),
        (2, "Ravi", "4000.00", 50.0),
    ]


def test_monthly_trend(client, sales_org):
    trend = client.get("/api/v1/analytics/trend?start=2026-09-01&end=2026-10-31", headers=sales_org["admin"]).json()
    assert [(t["period"], t["revenue"], t["orders"], t["target"]) for t in trend] == [
        ("2026-09-01", "2000.00", 1, None),
        ("2026-10-01", "9000.00", 3, "13000.00"),
    ]


def test_top_customers_and_products(client, sales_org):
    admin = sales_org["admin"]
    customers = client.get(f"/api/v1/analytics/top-customers?{OCT}", headers=admin).json()
    assert [(c["name"], c["revenue"], c["share_pct"]) for c in customers] == [
        ("Mehta Mart", "5000.00", 55.6),
        ("Gupta Stores", "4000.00", 44.4),
    ]
    products = client.get(f"/api/v1/analytics/top-products?{OCT}", headers=admin).json()
    assert [(p["sku"], p["revenue"], p["units"]) for p in products] == [
        ("P1", "7000.00", "7.000"),
        ("P2", "2000.00", "4.000"),
    ]


def test_invalid_date_range(client, sales_org):
    admin = sales_org["admin"]
    assert client.get("/api/v1/analytics/overview?start=2026-10-31&end=2026-10-01", headers=admin).status_code == 422
    assert client.get("/api/v1/analytics/overview?start=2020-01-01&end=2026-10-01", headers=admin).status_code == 422


def test_executive_target_view(client, sales_org):
    ravi = auth_headers(client, "ravi@acme.in")
    o = client.get(f"/api/v1/analytics/overview?{OCT}", headers=ravi).json()
    assert (o["revenue"], o["target"], o["achievement_pct"], o["target_gap"]) == ("4000.00", "8000.00", 50.0, "4000.00")


# --- Pure metric and period functions ----------------------------------------------------


def test_metric_definitions():
    assert metrics.achievement_pct(Decimal("850000"), Decimal("1200000")) == 70.8
    assert metrics.achievement_pct(Decimal("1"), None) is None
    assert metrics.target_gap(Decimal("1200000"), Decimal("850000")) == Decimal("350000")
    assert metrics.average_order_value(Decimal("100"), 0) is None
    assert metrics.growth_pct(Decimal("850000"), Decimal("1020000")) == -16.7
    assert metrics.growth_pct(Decimal("5"), Decimal("0")) is None
    assert metrics.required_daily_run_rate(Decimal("350000"), 10) == Decimal("35000.00")
    assert metrics.required_daily_run_rate(Decimal("-5"), 10) is None


def test_periods():
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 15), -2) == date(2025, 11, 15)
    # month-to-date compares with the same days last month
    assert previous_period(date(2026, 11, 1), date(2026, 11, 15)) == (date(2026, 10, 1), date(2026, 10, 15))
    # full month compares with full previous month
    assert previous_period(date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28))
    # arbitrary range compares with the preceding window of equal length
    assert previous_period(date(2026, 10, 10), date(2026, 10, 19)) == (date(2026, 9, 30), date(2026, 10, 9))
