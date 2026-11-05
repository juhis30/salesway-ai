import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str, **kwargs) -> Engine:
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    elif url.startswith("postgresql"):
        # Hosted Postgres (Neon) closes idle connections and sits behind a connection pooler:
        # check connections before use, recycle them, and don't rely on server-side prepared statements.
        kwargs.setdefault("pool_pre_ping", True)
        kwargs.setdefault("pool_recycle", 300)
        kwargs.setdefault("connect_args", {"prepare_threshold": None})
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # SQLite ignores foreign keys (and ON DELETE CASCADE) unless enabled per connection.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Row-level security (Postgres) ----------------------------------------------------------
#
# Every tenant table has a policy (migration 1aa74961f6b0) that shows a row only when its
# organization_id equals the transaction's app.current_org, or app.bypass_rls is on. With neither
# set, nothing is visible: the database fails closed even if application code forgets a filter.
# Settings are transaction-local (safe behind a connection pooler) and re-applied on every new
# transaction from session.info. SQLite has no RLS; there the application filters are the boundary.


def _apply_rls(session: Session, connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    org = session.info.get("org_id")
    connection.execute(
        text("SELECT set_config('app.current_org', :org, true), set_config('app.bypass_rls', :bypass, true)"),
        {"org": str(org) if org else "", "bypass": "on" if session.info.get("bypass_rls") else "off"},
    )


@event.listens_for(Session, "after_begin")
def _rls_on_begin(session: Session, _transaction, connection: Connection) -> None:
    _apply_rls(session, connection)


def _reapply(session: Session) -> None:
    if session.in_transaction():
        _apply_rls(session, session.connection())


def set_tenant(session: Session, organization_id: uuid.UUID | str | None) -> None:
    """Act for one organisation: from now on the database shows only its rows."""
    session.info["org_id"] = str(organization_id) if organization_id else None
    session.info["bypass_rls"] = False
    _reapply(session)


@contextmanager
def rls_bypass(session: Session) -> Iterator[None]:
    """For the few lookups that must span organisations (login by email, webhook routing, schedulers)."""
    previous = session.info.get("bypass_rls", False)
    session.info["bypass_rls"] = True
    _reapply(session)
    try:
        yield
    finally:
        session.info["bypass_rls"] = previous
        _reapply(session)


def system_session() -> Session:
    """A session for scripts and scheduled jobs that work across organisations."""
    session = SessionLocal()
    session.info["bypass_rls"] = True
    return session
