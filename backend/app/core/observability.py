"""Request IDs, structured access logs, safe error responses, security headers and optional Sentry."""

import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings

log = logging.getLogger("salesway")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# The API serves JSON, so it can forbid everything; the interactive docs page loads its own scripts.
API_CSP = "default-src 'none'; frame-ancestors 'none'"
DOCS_PATHS = ("/docs", "/redoc", "/api/v1/openapi.json")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                 "logger": record.name, "message": record.getMessage()}
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler()
    if settings.environment != "development":
        handler.setFormatter(JsonFormatter())  # one JSON object per line: searchable in Render's logs
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


class RequestContext(BaseHTTPMiddleware):
    """Tags each request with an id (returned as X-Request-ID), logs it, and adds security headers."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "")[:64] or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"fields": {"request_id": request_id, "path": request.url.path}})
            response = JSONResponse(
                status_code=500,
                content={"detail": f"Something went wrong on our side. Reference: {request_id}"},
            )
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith(DOCS_PATHS):
            response.headers.setdefault("Content-Security-Policy", API_CSP)
        if get_settings().environment != "development":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path != "/health":  # the platform pings this constantly
            log.info("request", extra={"fields": {
                "request_id": request_id, "method": request.method, "path": request.url.path,
                "status": response.status_code, "ms": duration_ms,
            }})
        return response


def database_status(engine: Engine) -> dict:
    """Is the database reachable, and does this database user really get row-level security?"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if conn.dialect.name != "postgresql":
                return {"database": "ok", "row_level_security": "not available (SQLite)"}
            superuser, bypass = conn.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"database": "unreachable", "error": type(exc).__name__}
    if superuser or bypass:
        return {"database": "ok", "row_level_security": "NOT ENFORCED: this database user bypasses it"}
    return {"database": "ok", "row_level_security": "enforced"}


def init_sentry() -> None:
    dsn = get_settings().sentry_dsn
    if not dsn:
        return
    import sentry_sdk

    sentry_sdk.init(dsn=dsn, environment=get_settings().environment, traces_sample_rate=0.1,
                    send_default_pii=False)  # no emails or IPs leave the server


def install(app: FastAPI, engine: Engine) -> None:
    configure_logging()
    init_sentry()
    app.add_middleware(RequestContext)

    @app.get("/health/ready", tags=["health"])
    def ready():
        status = database_status(engine)
        code = 200 if status["database"] == "ok" else 503
        return JSONResponse(status_code=code, content={"status": "ok" if code == 200 else "degraded", **status})

    @app.on_event("startup")
    def warn_if_rls_off():
        status = database_status(engine)
        if status.get("row_level_security", "").startswith("NOT ENFORCED"):
            log.warning("Row-level security is not enforced for this database user; see docs/security.md")
