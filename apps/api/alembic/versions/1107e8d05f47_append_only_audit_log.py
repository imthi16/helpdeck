"""append only audit log

Revision ID: 1107e8d05f47
Revises: 4595ed484a66
Create Date: 2026-07-15 06:40:00.000000

Task 5.4. The app role gets NO table privileges except SELECT: rows can only
be written through the SECURITY DEFINER ``audit_log_insert`` function, and
reads are org-scoped by a FOR SELECT-only RLS policy (there are deliberately
no INSERT/UPDATE/DELETE policies, so the SELECT grant cannot be parlayed into
writes). A BEFORE UPDATE/DELETE trigger raises even for the table owner, as a
belt-and-braces guard against superuser-path mistakes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1107e8d05f47"
down_revision: str | Sequence[str] | None = "4595ed484a66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_logs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            org_id uuid NOT NULL,
            actor_user_id uuid NULL,
            actor_type varchar(16) NOT NULL DEFAULT 'user',
            action varchar(64) NOT NULL,
            target_type varchar(32) NULL,
            target_id varchar(64) NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            ip varchar(64) NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_audit_logs_org_created ON audit_logs (org_id, created_at DESC)")

    # Reads: org-scoped SELECT only. Writes: none for the app role.
    op.execute(f"GRANT SELECT ON audit_logs TO {APP_ROLE}")
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_read ON audit_logs FOR SELECT "
        "USING (org_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)"
    )

    # The only write path. Owned by the migration user (bypasses the app
    # role's lack of privileges); search_path pinned; EXECUTE app-role only.
    op.execute(
        """
        CREATE FUNCTION audit_log_insert(
            p_org_id uuid,
            p_actor_user_id uuid,
            p_actor_type text,
            p_action text,
            p_target_type text,
            p_target_id text,
            p_payload jsonb,
            p_ip text
        ) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public
        AS $$
          INSERT INTO audit_logs
            (org_id, actor_user_id, actor_type, action, target_type, target_id, payload, ip)
          VALUES
            (p_org_id, p_actor_user_id, coalesce(p_actor_type, 'user'), p_action,
             p_target_type, p_target_id, coalesce(p_payload, '{}'::jsonb), p_ip)
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "audit_log_insert(uuid, uuid, text, text, text, text, jsonb, text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"audit_log_insert(uuid, uuid, text, text, text, text, jsonb, text) TO {APP_ROLE}"
    )

    # Append-only for everyone, including the owner.
    op.execute(
        """
        CREATE FUNCTION audit_logs_block_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_logs is append-only';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_mutation BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_mutation ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_block_mutation()")
    op.execute(
        "DROP FUNCTION IF EXISTS audit_log_insert(uuid, uuid, text, text, text, text, jsonb, text)"
    )
    op.execute("DROP POLICY IF EXISTS audit_read ON audit_logs")
    op.execute(f"REVOKE SELECT ON audit_logs FROM {APP_ROLE}")
    op.execute("DROP TABLE audit_logs")
