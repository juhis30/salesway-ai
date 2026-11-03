"""The connector framework shared by every CRM.

A connector knows three things about its system: how to sign in (OAuth), how to page through
records changed since a time, and how to translate a record into a SaleSway import row (the same
column names a CSV upload uses). Everything after that (validation, owner matching, duplicate
detection, writing) is the importer's job, so every source is held to the same rules.

Connectors only read. SaleSway never writes to a CRM.
"""

import base64
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.services.importer import SPECS
from app.models import ImportEntity

log = logging.getLogger(__name__)


class ConnectorError(RuntimeError):
    """The CRM call failed in a way retrying won't fix now."""


class AuthError(ConnectorError):
    """The CRM no longer accepts our credentials: the admin must connect again."""


@dataclass
class Tokens:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0  # epoch seconds

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at - 60

    def to_dict(self) -> dict:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token, "expires_at": self.expires_at}


@dataclass
class Connection:
    """What a successful sign-in produced."""

    tokens: Tokens
    settings: dict = field(default_factory=dict)
    account_label: str | None = None


# --- Credential encryption -------------------------------------------------------------------


def _fernet() -> Fernet:
    s = get_settings()
    secret = (s.credentials_key or s.jwt_secret).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def encrypt(tokens: Tokens) -> str:
    return _fernet().encrypt(json.dumps(tokens.to_dict()).encode()).decode()


def decrypt(blob: str | None) -> Tokens:
    if not blob:
        return Tokens()
    try:
        return Tokens(**json.loads(_fernet().decrypt(blob.encode())))
    except InvalidToken:
        raise AuthError("Stored credentials can't be read (the encryption key changed); connect again.") from None


# --- Import rows ----------------------------------------------------------------------------


def row(entity: str, **values) -> dict[str, str]:
    """An import row with every column of the entity present, as text (blank when unknown)."""
    out = {f.name: "" for f in SPECS[ImportEntity(entity)]}
    for name, value in values.items():
        if name not in out:
            raise KeyError(f"{entity} has no field {name}")
        out[name] = "" if value is None else str(value).strip()
    return out


def as_date(value: str | None) -> str:
    """'2026-10-05T10:30:00+05:30' or '2026-10-05' -> '2026-10-05'."""
    return (value or "")[:10]


def open_stage(probability: float | None) -> str:
    """Map an open deal to SaleSway's stages by its win probability (works for custom pipelines)."""
    p = probability if probability is not None else 0.2
    p = p / 100 if p > 1 else p
    if p < 0.2:
        return "prospecting"
    if p < 0.4:
        return "qualification"
    if p < 0.7:
        return "proposal"
    return "negotiation"


# --- HTTP with retries and token refresh ------------------------------------------------------

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


class ApiClient:
    """Makes a connector's API calls: adds auth, refreshes expired tokens once, retries throttling."""

    def __init__(self, connector: "Connector", tokens: Tokens, settings: dict, http: httpx.Client,
                 on_refresh: Callable[[Tokens], None] = lambda t: None, sleep: Callable[[float], None] = time.sleep,
                 context: dict | None = None):
        self.connector, self.tokens, self.settings, self.http = connector, tokens, settings, http
        self.on_refresh, self.sleep = on_refresh, sleep
        self.context = context or {}
        self.calls = 0

    def _refresh(self) -> None:
        self.tokens = self.connector.refresh(self.http, self.tokens)
        self.on_refresh(self.tokens)

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if self.tokens.refresh_token and self.tokens.expired:
            self._refresh()
        extra_headers = kwargs.pop("headers", {})
        refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            last = attempt == MAX_ATTEMPTS - 1
            self.calls += 1
            try:
                response = self.http.request(
                    method, url, headers={**extra_headers, **self.connector.auth_headers(self.tokens)}, **kwargs
                )
            except httpx.HTTPError as exc:
                if last:
                    raise ConnectorError(f"Network error calling {self.connector.label}: {exc}") from exc
                self.sleep(2**attempt)
                continue
            status = response.status_code
            if status == 401 and not refreshed and self.tokens.refresh_token:
                refreshed = True  # the access token expired early: refresh once and try again
                self._refresh()
                continue
            if status in RETRY_STATUSES and not last:
                wait = response.headers.get("Retry-After", "")
                self.sleep(min(float(wait), 30.0) if wait.replace(".", "", 1).isdigit() else 2**attempt)
                continue
            if status in (401, 403):
                raise AuthError(f"{self.connector.label} refused access ({status}); connect again.")
            if status >= 400:
                raise ConnectorError(f"{self.connector.label} returned {status}: {response.text[:200]}")
            return response
        raise ConnectorError(f"{self.connector.label} kept failing after {MAX_ATTEMPTS} attempts")


# --- Connector interface ---------------------------------------------------------------------


class Connector:
    provider = "base"
    label = "Base"
    description = ""
    # Synced in this order: later entities refer to earlier ones (orders to customers and products).
    entities: tuple[str, ...] = ()
    not_synced: str | None = None  # anything this system doesn't provide, said plainly in the UI
    required_settings: tuple[str, ...] = ()
    uses_oauth = True

    def missing_settings(self) -> list[str]:
        s = get_settings()
        return [name.upper() for name in self.required_settings if not getattr(s, name)]

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def exchange_code(self, http: httpx.Client, code: str, redirect_uri: str) -> Connection:
        raise NotImplementedError

    def refresh(self, http: httpx.Client, tokens: Tokens) -> Tokens:
        raise AuthError("This connection can't refresh its access; connect again.")

    def auth_headers(self, tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access_token}"}

    def fetch(self, api: ApiClient, entity: str, since: datetime | None) -> Iterator[dict[str, str]]:
        """Import rows for records created or changed since `since` (everything when None)."""
        raise NotImplementedError


def token_response(data: dict, previous: Tokens | None = None) -> Tokens:
    """Tokens from an OAuth token endpoint's JSON (refresh responses often omit the refresh token)."""
    if "access_token" not in data:
        raise AuthError(f"Sign-in failed: {data.get('error_description') or data.get('error') or 'no access token returned'}")
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or (previous.refresh_token if previous else ""),
        expires_at=time.time() + float(data["expires_in"]) if data.get("expires_in") else 0.0,
    )
