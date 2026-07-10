"""org onboarded flag

Revision ID: e785279bf923
Revises: 9d9c21511961
Create Date: 2026-07-10 21:57:38.880960

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e785279bf923"
down_revision: str | Sequence[str] | None = "9d9c21511961"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("onboarded", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("organizations", "onboarded")
