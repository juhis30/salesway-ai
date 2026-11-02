from app.tests.conftest import auth_headers, register_org


def _customer_id(client, headers, code):
    return next(c["id"] for c in client.get("/api/v1/customers", headers=headers).json()["items"] if c["code"] == code)


def test_customer_detail_summary(client, sales_org):
    admin = sales_org["admin"]
    c1 = _customer_id(client, admin, "C1")
    detail = client.get(f"/api/v1/customers/{c1}", headers=admin).json()
    assert detail["name"] == "Gupta Stores"
    assert detail["owner_name"] == "Ravi"
    assert detail["territory_name"] == "North"
    # SO-6 (Sep, 2000) + SO-1 (2500) + SO-2 (1500)
    assert (detail["lifetime_revenue"], detail["lifetime_orders"]) == ("6000.00", 3)
    assert (detail["first_order_date"], detail["last_order_date"]) == ("2026-09-10", "2026-10-15")


def test_products_bought_by_customer(client, sales_org):
    admin = sales_org["admin"]
    c1 = _customer_id(client, admin, "C1")
    rows = client.get(
        f"/api/v1/analytics/top-products?customer_id={c1}&start=2026-09-01&end=2026-10-31&limit=100", headers=admin
    ).json()
    summary = [(r["sku"], r["units"], r["revenue"], r["orders"], r["last_order_date"], r["average_unit_price"]) for r in rows]
    assert summary == [
        ("P1", "4.000", "4000.00", 2, "2026-10-05", "1000.00"),  # SO-6 + SO-1
        ("P2", "4.000", "2000.00", 2, "2026-10-15", "500.00"),   # SO-1 + SO-2
    ]
    overview = client.get(f"/api/v1/analytics/overview?customer_id={c1}&start=2026-10-01&end=2026-10-31", headers=admin).json()
    assert (overview["revenue"], overview["orders"], overview["target"]) == ("4000.00", 2, None)


def test_customer_orders_include_non_revenue_statuses(client, sales_org):
    admin = sales_org["admin"]
    c2 = _customer_id(client, admin, "C2")
    page = client.get(f"/api/v1/customers/{c2}/orders", headers=admin).json()
    assert page["total"] == 3
    assert [(o["order_number"], o["status"], o["counts_as_revenue"]) for o in page["items"]] == [
        ("SO-5", "pending", False),
        ("SO-4", "cancelled", False),
        ("SO-3", "confirmed", True),
    ]
    assert page["items"][2]["lines"] == [
        {"product_id": page["items"][2]["lines"][0]["product_id"], "sku": "P1", "product_name": "Atta 10kg",
         "quantity": "5.000", "unit_price": "1000.00", "line_amount": "5000.00"}
    ]


def test_customer_detail_access(client, sales_org):
    admin = sales_org["admin"]
    c2 = _customer_id(client, admin, "C2")  # Sita's customer
    ravi = auth_headers(client, "ravi@acme.in")
    assert client.get(f"/api/v1/customers/{c2}", headers=ravi).status_code == 403
    assert client.get(f"/api/v1/customers/{c2}/orders", headers=ravi).status_code == 403
    assert client.get(f"/api/v1/analytics/top-products?customer_id={c2}", headers=ravi).status_code == 403

    other = register_org(client, "Rival Co", "admin@rival.in")
    assert client.get(f"/api/v1/customers/{c2}", headers=other).status_code == 404
    assert client.get(f"/api/v1/analytics/overview?customer_id={c2}", headers=other).status_code == 404
