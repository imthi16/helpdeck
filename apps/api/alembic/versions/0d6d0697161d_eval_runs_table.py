"""eval runs table

Revision ID: 0d6d0697161d
Revises: 9bce7d9b3ba7
Create Date: 2026-07-15 09:30:00.000000

Quality-evaluation results (task 6.3): one row per eval run — CI fast gate,
nightly full RAGAS run, local full run, or online sampling. Platform-level
data (no org_id, no RLS); the app role can read it (the 6.6 quality surface)
and insert (the online-sampling worker).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0d6d0697161d"
down_revision: str | Sequence[str] | None = "9bce7d9b3ba7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),  # ci|nightly|local|online
        sa.Column("git_sha", sa.String(40), nullable=True),
        sa.Column("dataset", sa.String(32), nullable=False, server_default="golden"),
        sa.Column("item_count", sa.Integer, nullable=False),
        sa.Column("model_config", JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics", JSONB, nullable=False, server_default="{}"),
        sa.Column("thresholds", JSONB, nullable=False, server_default="{}"),
        sa.Column("passed", sa.Boolean, nullable=True),
        sa.Column("duration_s", sa.Float, nullable=True),
        sa.Column("report", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_eval_runs_created", "eval_runs", ["created_at"])
    op.execute(f"GRANT SELECT, INSERT ON eval_runs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT ON eval_runs FROM {APP_ROLE}")
    op.drop_index("ix_eval_runs_created", table_name="eval_runs")
    op.drop_table("eval_runs")
