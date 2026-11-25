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


def test_refresh_tokens_work_once(client, db_session):
    from datetime import datetime, timedelta, timezone

    from app.models import RefreshToken

    register_org(client, "Acme", "admin@acme.in")
    first = client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": PASSWORD}).json()
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    # Two tabs refreshing together present the same token; a second use moments later still works.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 200

    # Much later, the old token coming back means someone kept a copy: every session ends.
    for record in db_session.scalars(select(RefreshToken).where(RefreshToken.used_at.is_not(None))):
        record.used_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.flush()
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    newest = second.json()
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {newest['access_token']}"}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": newest["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "admin@acme.in", "password": PASSWORD}).status_code == 200


def test_sign_out_ends_only_this_device(client):
    register_org(client, "Acme", "admin@acme.in")
    login = {"email": "admin@acme.in", "password": PASSWORD}
    laptop = client.post("/api/v1/auth/login", json=login).json()
    phone = client.post("/api/v1/auth/login", json=login).json()
    swapped = client.post("/api/v1/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).json()

    assert client.post("/api/v1/auth/logout", json={"refresh_token": swapped["refresh_token"]}).status_code == 204
    # The laptop's whole chain of refresh tokens stops working; the phone carries on.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": swapped["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": phone["refresh_token"]}).status_code == 200
    # Signing out with a bad token is harmless.
    assert client.post("/api/v1/auth/logout", json={"refresh_token": "nonsense"}).status_code == 204


def test_sign_in_attempts_are_limited_per_address(client, monkeypatch):
    from app.api.v1 import auth

    monkeypatch.setattr(auth.login_limiter, "limit", 3)
    attempt = {"email": "nobody@acme.in", "password": "Wrong@12345"}
    for _ in range(3):
        assert client.post("/api/v1/auth/login", json=attempt).status_code == 401
    blocked = client.post("/api/v1/auth/login", json=attempt)
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers

    monkeypatch.setattr(auth.register_limiter, "limit", 1)
    register_org(client, "Acme", "admin@acme.in")
    again = client.post("/api/v1/auth/register", json={"organization_name": "Bharat Traders", "full_name": "Bina Rao", "email": "bina@bharat.in", "password": PASSWORD})
    assert again.status_code == 429


def test_tokens_survive_a_secret_rotation(client, monkeypatch):
    from app.core.config import get_settings

    headers = register_org(client, "Acme", "admin@acme.in")
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_previous_secrets", [settings.jwt_secret])
    monkeypatch.setattr(settings, "jwt_secret", "a-brand-new-secret-for-signing-tokens")
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200  # signed with the old secret
    monkeypatch.setattr(settings, "jwt_previous_secrets", [])
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401  # old secret retired
