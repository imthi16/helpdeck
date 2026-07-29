"""message langfuse trace id

Revision ID: c41d0a9e7b52
Revises: 9f24e6d603a1
Create Date: 2026-07-29 10:12:04.118902

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c41d0a9e7b52"
down_revision: str | Sequence[str] | None = "9f24e6d603a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Langfuse trace id of the turn that produced the message; lets thumbs
    # feedback arriving later be scored onto the right trace.
    op.add_column("messages", sa.Column("trace_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "trace_id")
