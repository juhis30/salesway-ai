from sqlalchemy import select

from app.models import Organization
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


# --- Login protection -----------------------------------------------------------------------

import pytest

from app.core.passwords import password_problem


@pytest.mark.parametrize("password, ok", [
    ("Secret@123", True), ("Demo@12345", True), ("short1", False), ("onlyletters", False),
    ("1234567890", False), ("Password123", False), ("ravi-2026-x9", False),  # contains the email name
])
def test_password_rules(password, ok):
    assert (password_problem(password, "ravi@acme.in") is None) == ok


def test_weak_password_rejected_on_register(client):
    response = client.post("/api/v1/auth/register", json={
        "organization_name": "Acme", "full_name": "A", "email": "admin@acme.in", "password": "password123"})
    assert response.status_code == 422 and "too common" in response.text


def test_lockout_after_repeated_wrong_passwords(client):
    register_org(client, "Acme", "admin@acme.in")
    wrong = {"email": "admin@acme.in", "password": "Wrong@12345"}
    for _ in range(4):
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    assert client.post("/api/v1/auth/login", json=wrong).status_code == 401  # 5th: now locked
    locked = client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": PASSWORD})
    assert locked.status_code == 429 and "Try again in 15 minutes" in locked.json()["detail"]


def test_successful_login_resets_the_count(client):
    register_org(client, "Acme", "admin@acme.in")
    wrong = {"email": "admin@acme.in", "password": "Wrong@12345"}
    for _ in range(4):
        client.post("/api/v1/auth/login", json=wrong)
    assert client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": PASSWORD}).status_code == 200
    for _ in range(4):
        client.post("/api/v1/auth/login", json=wrong)
    assert client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": PASSWORD}).status_code == 200


def test_changing_password_signs_out_other_sessions(client):
    register_org(client, "Acme", "admin@acme.in")
    laptop = client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": PASSWORD}).json()
    phone = auth_headers(client, "admin@acme.in")

    bad = client.post("/api/v1/auth/change-password", headers=phone, json={"current_password": "nope", "new_password": "Newer@54321"})
    assert bad.status_code == 400
    weak = client.post("/api/v1/auth/change-password", headers=phone, json={"current_password": PASSWORD, "new_password": "abc"})
    assert weak.status_code == 422
    fresh = client.post("/api/v1/auth/change-password", headers=phone, json={"current_password": PASSWORD, "new_password": "Newer@54321"})
    assert fresh.status_code == 200

    # The phone's old token and the laptop's tokens (access and refresh) no longer work.
    assert client.get("/api/v1/auth/me", headers=phone).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {laptop['access_token']}"}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).status_code == 401
    # The new tokens and the new password do.
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh.json()['access_token']}"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": "Newer@54321"}).status_code == 200


def test_admin_reset_unlocks_and_signs_out(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    rep = client.post("/api/v1/users", headers=admin, json={
        "email": "rep@acme.in", "full_name": "Rep", "password": PASSWORD, "role": "sales_executive"}).json()
    rep_headers = auth_headers(client, "rep@acme.in")
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "rep@acme.in", "password": "Wrong@12345"})
    assert client.post("/api/v1/auth/login", json={"email": "rep@acme.in", "password": PASSWORD}).status_code == 429

    assert client.post(f"/api/v1/users/{rep['id']}/reset-password", headers=rep_headers, json={"new_password": "Temp@98765"}).status_code == 403
    assert client.post(f"/api/v1/users/{rep['id']}/reset-password", headers=admin, json={"new_password": "Temp@98765"}).status_code == 204
    assert client.get("/api/v1/auth/me", headers=rep_headers).status_code == 401  # signed out
    assert client.post("/api/v1/auth/login", json={"email": "rep@acme.in", "password": "Temp@98765"}).status_code == 200  # unlocked


def test_shared_demo_passwords_cannot_be_changed_or_locked(client, db_session):
    admin = register_org(client, "Demo Co", "admin@demo.in")
    db_session.scalar(select(Organization).where(Organization.name == "Demo Co")).plan = "demo"
    db_session.flush()
    me = client.get("/api/v1/auth/me", headers=admin).json()["user"]

    change = client.post("/api/v1/auth/change-password", headers=admin, json={"current_password": PASSWORD, "new_password": "Newer@54321"})
    assert change.status_code == 403 and "shared demo" in change.json()["detail"]
    reset = client.post(f"/api/v1/users/{me['id']}/reset-password", headers=admin, json={"new_password": "Temp@98765"})
    assert reset.status_code == 403
    for _ in range(8):  # one visitor's wrong guesses never lock everyone else out
        client.post("/api/v1/auth/login", json={"email": "admin@demo.in", "password": "Wrong@12345"})
    assert client.post("/api/v1/auth/login", json={"email": "admin@demo.in", "password": PASSWORD}).status_code == 200


def test_role_change_signs_the_user_out(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    rep = client.post("/api/v1/users", headers=admin, json={
        "email": "rep@acme.in", "full_name": "Rep", "password": PASSWORD, "role": "analyst"}).json()
    rep_headers = auth_headers(client, "rep@acme.in")
    client.patch(f"/api/v1/users/{rep['id']}", headers=admin, json={"role": "sales_executive"})
    assert client.get("/api/v1/auth/me", headers=rep_headers).status_code == 401
