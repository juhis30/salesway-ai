from app.tests.conftest import PASSWORD, auth_headers, register_org


def test_register_creates_org_and_admin(client):
    headers = register_org(client, "Acme Traders", "Admin@Acme.in")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["user"]["email"] == "admin@acme.in"
    assert me["user"]["role"] == "org_admin"
    assert me["organization"]["name"] == "Acme Traders"
    assert me["organization"]["timezone"] == "Asia/Kolkata"


def test_duplicate_email_rejected(client):
    register_org(client, "Acme", "admin@acme.in")
    response = client.post(
        "/api/v1/auth/register",
        json={"organization_name": "Other", "full_name": "X", "email": "ADMIN@acme.in", "password": PASSWORD},
    )
    assert response.status_code == 409


def test_login_wrong_password(client):
    register_org(client, "Acme", "admin@acme.in")
    response = client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": "wrong-pass"})
    assert response.status_code == 401
    response = client.post("/api/v1/auth/login", json={"email": "nobody@acme.in", "password": "wrong-pass"})
    assert response.status_code == 401


def test_refresh_token_flow(client):
    register_org(client, "Acme", "admin@acme.in")
    tokens = client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": PASSWORD}).json()

    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200

    # An access token cannot be used as a refresh token, and vice versa.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401
    bad = {"Authorization": f"Bearer {tokens['refresh_token']}"}
    assert client.get("/api/v1/auth/me", headers=bad).status_code == 401


def test_protected_routes_need_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/analytics/overview").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_deactivated_user_cannot_log_in(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    user = client.post(
        "/api/v1/users", headers=admin,
        json={"email": "rep@acme.in", "full_name": "Rep", "password": PASSWORD, "role": "sales_executive"},
    ).json()
    rep = auth_headers(client, "rep@acme.in")

    client.patch(f"/api/v1/users/{user['id']}", headers=admin, json={"is_active": False})
    assert client.get("/api/v1/auth/me", headers=rep).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "rep@acme.in", "password": PASSWORD}).status_code == 401
