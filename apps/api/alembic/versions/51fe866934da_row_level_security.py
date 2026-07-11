"""row level security

Revision ID: 51fe866934da
Revises: bf1f6f4635af
Create Date: 2026-07-11 09:52:31.310203

Creates the non-superuser ``helpdeck_app`` role the application serves requests
as, and enables FORCE row-level security with tenant-isolation policies on the
tenant data tables. The app sets ``app.current_tenant`` per transaction; the
superuser (migrations/seed/tests) bypasses RLS.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "51fe866934da"
down_revision: str | Sequence[str] | None = "bf1f6f4635af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tenant data tables scoped by org_id.
RLS_TABLES = ("documents", "chunks", "conversations", "messages", "escalations")

APP_ROLE = "helpdeck_app"
# Dev-only default password; production sets APP_DATABASE_URL with a real secret.
APP_PASSWORD = "helpdeck_app"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'
              NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
          END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )

    tenant_predicate = "org_id = current_setting('app.current_tenant', true)::uuid"
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({tenant_predicate}) WITH CHECK ({tenant_predicate})"
        )


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {APP_ROLE}")
