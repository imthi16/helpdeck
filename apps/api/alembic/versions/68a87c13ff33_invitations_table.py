"""invitations table

Revision ID: 68a87c13ff33
Revises: 677bd6a813f4
Create Date: 2026-07-15 05:57:06.984169

Pending member invites, redeemed by a copyable token link (no SMTP: the full
URL is shown once at creation; only the token's sha256 is stored). Identity-
lane table: acceptance happens before the accepting user has any membership,
so a tenant-keyed RLS policy could never match — access control lives in the
members router (admin+ to create/list/revoke, possession of the token to
redeem).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, UUID

from alembic import op

revision: str = "68a87c13ff33"
down_revision: str | Sequence[str] | None = "677bd6a813f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "role",
            ENUM("owner", "admin", "agent", "viewer", name="membership_role", create_type=False),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "invited_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_invitations_org_id", "invitations", ["org_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON invitations FROM {APP_ROLE}")
    op.drop_index("ix_invitations_org_id", table_name="invitations")
    op.drop_table("invitations")
