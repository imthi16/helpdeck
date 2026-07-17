"""message trace id

Revision ID: 9bce7d9b3ba7
Revises: bc62d0aae57b
Create Date: 2026-07-15 08:20:00.000000

Stores the Langfuse/W3C trace id (32 hex chars) on assistant messages so
feedback arriving later (thumbs, CSAT, online eval) can be attached to the
right trace, and the dashboard can deep-link to it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9bce7d9b3ba7"
down_revision: str | Sequence[str] | None = "bc62d0aae57b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("trace_id", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "trace_id")
