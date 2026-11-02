from app.tests.conftest import auth_headers, create_user, register_org, upload


def test_other_org_sees_none_of_our_data(client, sales_org):
    other = register_org(client, "Rival Co", "admin@rival.in")

    assert client.get("/api/v1/customers", headers=other).json()["total"] == 0
    assert client.get("/api/v1/products", headers=other).json()["total"] == 0
    assert [u["email"] for u in client.get("/api/v1/users", headers=other).json()] == ["admin@rival.in"]
    assert client.get("/api/v1/imports", headers=other).json() == []

    overview = client.get("/api/v1/analytics/overview?start=2026-10-01&end=2026-10-31", headers=other).json()
    assert overview["revenue"] == "0"
    assert overview["orders"] == 0
    assert overview["data_as_of"] is None


def test_cannot_touch_another_orgs_records(client, sales_org):
    other = register_org(client, "Rival Co", "admin@rival.in")
    ravi_id = sales_org["ravi"]["id"]

    # Filtering analytics by another org's salesperson is a 404, not a data leak.
    assert client.get(f"/api/v1/analytics/overview?salesperson_id={ravi_id}", headers=other).status_code == 404
    assert client.patch(f"/api/v1/users/{ravi_id}", headers=other, json={"full_name": "Hacked"}).status_code == 404

    job = client.get("/api/v1/imports", headers=sales_org["admin"]).json()[0]
    assert client.get(f"/api/v1/imports/{job['id']}/errors", headers=other).status_code == 404
    assert client.post(f"/api/v1/imports/{job['id']}/confirm", headers=other).status_code == 404


def test_imports_resolve_references_only_within_own_org(client, sales_org):
    """Another org's customer codes, SKUs and emails must not be usable in our imports."""
    other = register_org(client, "Rival Co", "admin@rival.in")
    preview = upload(client, other, "orders",
                     "order_no,order_date,customer_code,salesperson_email,sku,qty,rate\n"
                     "X-1,2026-10-01,C1,ravi@acme.in,P1,1,100\n", confirm=False)
    fields = {e["field"] for e in preview["errors"]}
    assert {"customer", "product", "salesperson_email"} <= fields
    assert preview["job"]["valid_rows"] == 0


def test_executive_sees_only_own_data(client, sales_org):
    ravi = auth_headers(client, "ravi@acme.in")

    board = client.get("/api/v1/analytics/leaderboard?start=2026-10-01&end=2026-10-31", headers=ravi).json()
    assert [row["name"] for row in board] == ["Ravi"]
    overview = client.get("/api/v1/analytics/overview?start=2026-10-01&end=2026-10-31", headers=ravi).json()
    assert overview["revenue"] == "4000.00"

    customers = client.get("/api/v1/customers", headers=ravi).json()
    assert [c["name"] for c in customers["items"]] == ["Gupta Stores"]

    sita_id = sales_org["sita"]["id"]
    assert client.get(f"/api/v1/analytics/overview?salesperson_id={sita_id}", headers=ravi).status_code == 403


def test_manager_sees_their_team_only(client, sales_org):
    admin = sales_org["admin"]
    team_a = client.post("/api/v1/teams", headers=admin, json={"name": "Team A"}).json()
    client.patch(f"/api/v1/users/{sales_org['ravi']['id']}", headers=admin, json={"team_id": team_a["id"]})
    create_user(client, admin, "boss@acme.in", "sales_manager", team_id=team_a["id"])
    boss = auth_headers(client, "boss@acme.in")

    names = sorted(u["full_name"] for u in client.get("/api/v1/users", headers=boss).json())
    assert names == ["Boss", "Ravi"]
    overview = client.get("/api/v1/analytics/overview?start=2026-10-01&end=2026-10-31", headers=boss).json()
    assert overview["revenue"] == "4000.00"


def test_regional_manager_sees_whole_region(client, sales_org):
    admin = sales_org["admin"]
    north_1 = client.post("/api/v1/territories", headers=admin, json={"name": "Delhi", "region": "North"}).json()
    north_2 = client.post("/api/v1/territories", headers=admin, json={"name": "Punjab", "region": "North"}).json()
    client.patch(f"/api/v1/users/{sales_org['ravi']['id']}", headers=admin, json={"territory_id": north_2["id"]})
    create_user(client, admin, "rm@acme.in", "regional_manager", territory_id=north_1["id"])
    rm = auth_headers(client, "rm@acme.in")

    assert sorted(u["full_name"] for u in client.get("/api/v1/users", headers=rm).json()) == ["Ravi", "Rm"]


def test_admin_only_actions(client, sales_org):
    ravi = auth_headers(client, "ravi@acme.in")
    body = {"email": "x@acme.in", "full_name": "X", "password": "Secret@123", "role": "org_admin"}
    assert client.post("/api/v1/users", headers=ravi, json=body).status_code == 403
    assert client.post("/api/v1/teams", headers=ravi, json={"name": "T"}).status_code == 403
    assert client.patch("/api/v1/organization", headers=ravi, json={"name": "Mine now"}).status_code == 403
    files = {"file": ("c.csv", b"customer_name\nX\n", "text/csv")}
    assert client.post("/api/v1/imports/customers/preview", headers=ravi, files=files).status_code == 403


def test_org_admin_cannot_grant_super_admin_or_demote_self(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    body = {"email": "x@acme.in", "full_name": "X", "password": "Secret@123", "role": "super_admin"}
    assert client.post("/api/v1/users", headers=admin, json=body).status_code == 403

    me = client.get("/api/v1/auth/me", headers=admin).json()["user"]
    response = client.patch(f"/api/v1/users/{me['id']}", headers=admin, json={"role": "analyst"})
    assert response.status_code == 422
