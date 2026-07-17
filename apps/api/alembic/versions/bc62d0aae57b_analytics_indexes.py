"""analytics indexes

Revision ID: bc62d0aae57b
Revises: 1107e8d05f47
Create Date: 2026-07-15 07:05:00.000000

Composite (org_id, created_at) indexes backing the analytics window queries
(task 5.5). A nightly rollup table is deliberately deferred — at current
volumes these are cheap index scans; see ROADMAP.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bc62d0aae57b"
down_revision: str | Sequence[str] | None = "1107e8d05f47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_conversations_org_created", "conversations", ["org_id", "created_at"])
    op.create_index("ix_messages_org_created", "messages", ["org_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_messages_org_created", table_name="messages")
    op.drop_index("ix_conversations_org_created", table_name="conversations")
