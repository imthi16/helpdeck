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

from sqlalchemy.engine import make_url

from alembic import op
from app.core.config import get_settings

revision: str = "51fe866934da"
down_revision: str | Sequence[str] | None = "bf1f6f4635af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tenant data tables scoped by org_id.
RLS_TABLES = ("documents", "chunks", "conversations", "messages", "escalations")

APP_ROLE = "helpdeck_app"

# Tenant child tables and the parent they must stay within the same tenant as.
# (child, child_fk_col, parent, old_fkey, new_fkey)
CHILD_PARENTS = (
    ("chunks", "document_id", "documents", "chunks_document_id_fkey", "chunks_document_org_fkey"),
    (
        "messages",
        "conversation_id",
        "conversations",
        "messages_conversation_id_fkey",
        "messages_conversation_org_fkey",
    ),
    (
        "escalations",
        "conversation_id",
        "conversations",
        "escalations_conversation_id_fkey",
        "escalations_conversation_org_fkey",
    ),
)


def _app_password() -> str | None:
    """The app role's password, derived from the deployment secret APP_DATABASE_URL.

    We never bake a fixed credential into the migration: the role password is
    whatever the operator configured for the app connection. If the role is
    provisioned out of band (prod), ``CREATE ROLE`` below is skipped entirely.
    """
    return make_url(get_settings().app_database_url).password


def upgrade() -> None:
    # --- app role -----------------------------------------------------------
    password = _app_password()
    if password is not None:
        # Escape single quotes for the SQL string literal; the DO block uses a
        # named dollar-quote tag so a "$$" in the password cannot break out.
        login_clause = "LOGIN PASSWORD '" + password.replace("'", "''") + "'"
    else:
        # No password available (role expected to be provisioned externally);
        # create a login role without a password rather than a known default.
        login_clause = "LOGIN"

    op.execute(
        f"""
        DO $rolesetup$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} {login_clause}
              NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
          END IF;
        END
        $rolesetup$;
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

    # --- keep tenant child rows tied to a parent in the SAME tenant ---------
    # FK referential-integrity checks bypass RLS, so a policy that only pins the
    # child's org_id is not enough: an app-role insert scoped to tenant A could
    # create a child (org_id=A) pointing at tenant B's parent. Composite
    # (id, org_id) FKs force the parent's org_id to equal the child's, which
    # WITH CHECK already constrains to the current tenant.
    for parent in ("documents", "conversations"):
        op.execute(f"ALTER TABLE {parent} ADD CONSTRAINT uq_{parent}_id_org UNIQUE (id, org_id)")
    for child, fk_col, parent, old_fkey, new_fkey in CHILD_PARENTS:
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {old_fkey}")
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {new_fkey} "
            f"FOREIGN KEY ({fk_col}, org_id) REFERENCES {parent} (id, org_id) ON DELETE CASCADE"
        )

    # --- row-level security -------------------------------------------------
    # NULLIF guards the fail-closed path: a pooled connection whose only tenant
    # setting was transaction-local reverts to '' (not NULL) after that txn, and
    # ''::uuid would raise instead of returning zero rows. Coerce '' -> NULL so
    # an unset tenant matches no rows.
    tenant_predicate = "org_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"
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

    for child, fk_col, parent, old_fkey, new_fkey in CHILD_PARENTS:
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {new_fkey}")
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {old_fkey} "
            f"FOREIGN KEY ({fk_col}) REFERENCES {parent} (id) ON DELETE CASCADE"
        )
    for parent in ("documents", "conversations"):
        op.execute(f"ALTER TABLE {parent} DROP CONSTRAINT IF EXISTS uq_{parent}_id_org")

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
