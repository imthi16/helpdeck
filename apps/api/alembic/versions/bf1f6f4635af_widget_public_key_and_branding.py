"""widget public key and branding

Revision ID: bf1f6f4635af
Revises: e785279bf923
Create Date: 2026-07-10 22:09:46.249643

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf1f6f4635af"
down_revision: str | Sequence[str] | None = "e785279bf923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "widget_allowed_origins", sa.String(length=2048), server_default="", nullable=False
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "widget_welcome_message",
            sa.String(length=500),
            server_default="Hi! How can I help you today?",
            nullable=False,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("widget_color", sa.String(length=16), server_default="#4f46e5", nullable=False),
    )
    # public_key is unique + not null; add nullable, backfill, then constrain.
    op.add_column("organizations", sa.Column("public_key", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE organizations "
        "SET public_key = 'pk_' || replace(gen_random_uuid()::text, '-', '') "
        "WHERE public_key IS NULL"
    )
    op.alter_column("organizations", "public_key", nullable=False)
    op.create_unique_constraint("uq_organizations_public_key", "organizations", ["public_key"])
    op.add_column("messages", sa.Column("feedback", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "feedback")
    op.drop_constraint("uq_organizations_public_key", "organizations", type_="unique")
    op.drop_column("organizations", "public_key")
    op.drop_column("organizations", "widget_color")
    op.drop_column("organizations", "widget_welcome_message")
    op.drop_column("organizations", "widget_allowed_origins")
