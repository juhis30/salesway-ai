"""Row-level security: the database itself keeps organisations apart (Postgres only).

Run with TEST_DATABASE_URL pointing at Postgres; on SQLite these tests are skipped.
"""

import uuid

import pytest
from fastapi import Depends
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import get_db, rls_bypass, set_tenant
from app.core.deps import get_current_user
from app.main import app
from app.models import Customer, Organization, User
from app.tests.conftest import POSTGRES_URL, auth_headers, register_org, upload

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="row-level security needs Postgres (set TEST_DATABASE_URL)")


@pytest.fixture
def two_orgs(client, sales_org):
    rival = register_org(client, "Rival Co", "admin@rival.in")
    upload(client, rival, "customers", "customer_code,customer_name\nR1,Rival Customer\n")
    return {"acme": sales_org["admin"], "rival": rival}


def org_id(client, headers):
    return uuid.UUID(client.get("/api/v1/auth/me", headers=headers).json()["organization"]["id"])


def test_every_tenant_table_is_protected(db_session):
    tables = db_session.execute(text("""
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               EXISTS (SELECT 1 FROM pg_policies p WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND (EXISTS (SELECT 1 FROM information_schema.columns col
                       WHERE col.table_name = c.relname AND col.column_name = 'organization_id')
               OR c.relname = 'organizations')
    """)).all()
    assert len(tables) == 21
    unprotected = [name for name, enabled, forced, policy in tables if not (enabled and forced and policy)]
    assert unprotected == [], f"add these tables to the row-level security migration: {unprotected}"


def test_database_shows_only_the_current_organisation(client, db_session, two_orgs):
    acme = org_id(client, two_orgs["acme"])
    rival = org_id(client, two_orgs["rival"])
    db_session.info["bypass_rls"] = False

    set_tenant(db_session, acme)
    names = set(db_session.scalars(select(Customer.name)))  # no organisation filter in the query
    assert names == {"Gupta Stores", "Mehta Mart"}
    assert db_session.scalar(select(func.count()).select_from(Organization)) == 1

    set_tenant(db_session, rival)
    assert set(db_session.scalars(select(Customer.name))) == {"Rival Customer"}

    set_tenant(db_session, None)  # nobody: nothing at all
    assert db_session.scalar(select(func.count()).select_from(User)) == 0
    assert db_session.scalar(select(func.count()).select_from(Customer)) == 0

    with rls_bypass(db_session):
        assert db_session.scalar(select(func.count()).select_from(Customer)) == 3


def test_cannot_write_into_another_organisation(client, db_session, two_orgs):
    acme = org_id(client, two_orgs["acme"])
    rival = org_id(client, two_orgs["rival"])
    db_session.info["bypass_rls"] = False
    set_tenant(db_session, acme)
    savepoint = db_session.begin_nested()
    db_session.add(Customer(organization_id=rival, name="Planted"))
    with pytest.raises(DBAPIError, match="row-level security"):
        db_session.flush()
    savepoint.rollback()


def test_a_forgotten_filter_in_an_endpoint_still_cannot_leak(client, two_orgs):
    """Simulates a future bug: an endpoint that queries customers with no organisation filter."""

    @app.get("/api/v1/_test/all-customers")
    def careless(user: User = Depends(get_current_user), db=Depends(get_db)):
        return sorted(db.scalars(select(Customer.name)))

    try:
        assert client.get("/api/v1/_test/all-customers", headers=two_orgs["rival"]).json() == ["Rival Customer"]
        assert client.get("/api/v1/_test/all-customers", headers=two_orgs["acme"]).json() == ["Gupta Stores", "Mehta Mart"]
        ravi = auth_headers(client, "ravi@acme.in")
        assert "Rival Customer" not in client.get("/api/v1/_test/all-customers", headers=ravi).json()
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/v1/_test/all-customers"]
