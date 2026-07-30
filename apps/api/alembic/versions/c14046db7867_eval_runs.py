"""eval runs

Revision ID: c14046db7867
Revises: c41d0a9e7b52
Create Date: 2026-07-29 22:38:09.128649

Evaluation run history: one row per offline eval run over the golden set (6.3),
later per online sampling run (6.5).

A system table rather than tenant data — a golden run scores the pipeline against
a fixture corpus, so there is no org_id and no RLS, as with audit_logs. The app
role gets SELECT only: 6.6's in-product Quality card reads the latest row, while
runs are written by the eval CLI as the superuser.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c14046db7867"
down_revision: str | Sequence[str] | None = "c41d0a9e7b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("dataset", sa.String(length=128), nullable=False),
        sa.Column("subset", sa.String(length=32), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "environment",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=True),
        sa.Column("report_path", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # "latest run" and the quality trend are both created_at-ordered reads.
    op.create_index("ix_eval_runs_created_at", "eval_runs", ["created_at"], unique=False)

    # Read-only for the app: the dashboard shows eval scores, it never writes them.
    op.execute(f"GRANT SELECT ON eval_runs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON eval_runs FROM {APP_ROLE}")
    op.drop_index("ix_eval_runs_created_at", table_name="eval_runs")
    op.drop_table("eval_runs")
