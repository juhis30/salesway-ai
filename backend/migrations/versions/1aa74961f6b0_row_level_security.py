"""row level security

Every organisation-owned table only shows rows of the organisation the current transaction acts
for (app.current_org), unless app.bypass_rls is on. FORCE applies the policy to the table owner
too, which is the role the app connects as. Postgres only; SQLite has no RLS.

Revision ID: 1aa74961f6b0
Revises: 14bdafd0868a
Create Date: 2026-11-03 17:18:28.238117

"""
from typing import Sequence, Union

from alembic import op

revision: str = '1aa74961f6b0'
down_revision: Union[str, None] = '14bdafd0868a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = [
    "audit_logs", "conversations", "customers", "import_jobs", "integrations", "messages", "ml_models",
    "opportunities", "predictions", "products", "recommendations", "sales_activities", "sales_order_items",
    "sales_orders", "sales_targets", "sync_jobs", "teams", "territories", "users", "whatsapp_accounts",
]
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
CURRENT = "nullif(current_setting('app.current_org', true), '')::uuid"


def _protect(table: str, column: str) -> None:
    rule = f"({BYPASS} OR {column} = {CURRENT})"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {rule} WITH CHECK {rule}")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        _protect(table, "organization_id")
    _protect("organizations", "id")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in [*TENANT_TABLES, "organizations"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
