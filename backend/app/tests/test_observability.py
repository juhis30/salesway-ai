"""Security headers, request ids, safe 500s and the readiness check."""

from fastapi import APIRouter

from app.main import app


def test_security_headers_and_request_id(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy", "X-Request-ID"):
        assert header in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"
    # A request id sent by a caller (e.g. Render's router) is kept, so logs can be matched up.
    assert client.get("/health", headers={"X-Request-ID": "abc123"}).headers["X-Request-ID"] == "abc123"
    # The docs page loads its own scripts, so it doesn't get the strict API policy.
    assert "Content-Security-Policy" not in client.get("/docs").headers


def test_unexpected_errors_hide_details(client):
    router = APIRouter()

    @router.get("/api/v1/_test/boom")
    def boom():
        raise RuntimeError("secret internal detail: password=hunter2")

    app.include_router(router)
    try:
        response = client.get("/api/v1/_test/boom")
        assert response.status_code == 500
        body = response.json()["detail"]
        assert "hunter2" not in body and response.headers["X-Request-ID"] in body
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/v1/_test/boom"]


def test_readiness_reports_the_database(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
