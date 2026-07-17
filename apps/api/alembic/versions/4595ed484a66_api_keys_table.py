"""api keys table

Revision ID: 4595ed484a66
Revises: 68a87c13ff33
Create Date: 2026-07-15 06:15:07.262829

The org's API keys (task 5.3): public widget key(s) — the Phase 4
``organizations.public_key``, now revocable and tracked — plus reveal-once
secret keys. Tenant table with FORCE RLS (dashboard CRUD flows through
``tenant_session``). Widget auth happens *before* a tenant is known, so the
lookup goes through SECURITY DEFINER functions owned by the migration user:
``resolve_api_key(hash)`` (id/org/type for an unrevoked key) and
``touch_api_key(id)`` (bump ``last_used_at``). Existing orgs are backfilled
with one widget row mirroring ``organizations.public_key``; the org column is
kept for now (onboarding/seed compatibility) but no longer used for auth.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "4595ed484a66"
down_revision: str | Sequence[str] | None = "68a87c13ff33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"
TENANT_PREDICATE = "org_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("key_type", sa.String(16), nullable=False),
        sa.Column("scopes", JSONB, nullable=False, server_default="[]"),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("public_value", sa.String(64), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("key_type IN ('widget', 'secret')", name="ck_api_keys_key_type"),
    )
    op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"])

    # Tenant isolation, same posture as the other tenant tables (5.1).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys TO {APP_ROLE}")
    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON api_keys "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )

    # Pre-tenant lookup + last-used bump for widget auth. SECURITY DEFINER (the
    # migration user owns them and bypasses RLS); pinned search_path; EXECUTE
    # revoked from PUBLIC and granted only to the app role.
    op.execute(
        """
        CREATE FUNCTION resolve_api_key(p_hash text)
        RETURNS TABLE(key_id uuid, org_id uuid, key_type text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
        AS $$
          SELECT id, org_id, key_type FROM api_keys
          WHERE secret_hash = p_hash AND revoked_at IS NULL
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION touch_api_key(p_key_id uuid)
        RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public
        AS $$
          UPDATE api_keys SET last_used_at = now() WHERE id = p_key_id
        $$
        """
    )
    for fn in ("resolve_api_key(text)", "touch_api_key(uuid)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_ROLE}")

    # Backfill: one widget key per existing org, mirroring organizations.public_key.
    op.execute(
        """
        INSERT INTO api_keys
          (id, org_id, name, key_type, scopes, prefix, secret_hash, public_value,
           created_at, updated_at)
        SELECT gen_random_uuid(), id, 'Widget key', 'widget', '[]'::jsonb,
               left(public_key, 9), encode(sha256(public_key::bytea), 'hex'), public_key,
               now(), now()
        FROM organizations
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS touch_api_key(uuid)")
    op.execute("DROP FUNCTION IF EXISTS resolve_api_key(text)")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON api_keys")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON api_keys FROM {APP_ROLE}")
    op.drop_index("ix_api_keys_org_id", table_name="api_keys")
    op.drop_table("api_keys")
