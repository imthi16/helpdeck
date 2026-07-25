"""invitations

Revision ID: 0ba1ec52cd01
Revises: 8bb42227bfe1
Create Date: 2026-07-25 14:07:49.680555

Pending invites to join an org, redeemed with a single-use hashed token. No RLS:
redeeming a token is a cross-tenant lookup made before any org context exists
(same rationale as ``users``/``memberships``), so the table is reached through the
identity session and is deliberately not granted to the ``helpdeck_app`` role.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0ba1ec52cd01"
down_revision: str | Sequence[str] | None = "8bb42227bfe1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "role",
            # The enum already exists (memberships.role); reuse it, don't recreate.
            postgresql.ENUM(
                "owner", "admin", "agent", "viewer", name="membership_role", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_invitations_org_id"), "invitations", ["org_id"], unique=False)
    # At most one live invite per email per org; accepted rows stay for history.
    op.create_index(
        "uq_invitations_org_email_pending",
        "invitations",
        ["org_id", "email"],
        unique=True,
        postgresql_where="accepted_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_invitations_org_email_pending",
        table_name="invitations",
        postgresql_where="accepted_at IS NULL",
    )
    op.drop_index(op.f("ix_invitations_org_id"), table_name="invitations")
    op.drop_table("invitations")
