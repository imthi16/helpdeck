"""audit logs

Revision ID: 9f24e6d603a1
Revises: fb3542d2ad85
Create Date: 2026-07-25 15:48:08.056693

An append-only audit trail. "Append-only" is enforced by privileges rather than
convention: the app role is granted SELECT + INSERT and nothing else, so the
application cannot rewrite or erase history even if a handler tried. The
superuser keeps full rights so ON DELETE CASCADE from organizations and test
cleanup still work.

No RLS, like the other identity-adjacent tables — a login event exists before any
org context does. Reads are scoped by an explicit org_id filter.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f24e6d603a1"
down_revision: str | Sequence[str] | None = "fb3542d2ad85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_org_created", "audit_logs", ["org_id", "created_at"], unique=False
    )

    # The whole point: writes and reads, but never UPDATE or DELETE.
    op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON audit_logs FROM {APP_ROLE}")
    op.drop_index("ix_audit_logs_org_created", table_name="audit_logs")
    op.drop_table("audit_logs")
