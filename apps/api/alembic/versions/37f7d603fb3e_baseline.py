"""baseline

Revision ID: 37f7d603fb3e
Revises:
Create Date: 2026-07-09 20:06:52.317062

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "37f7d603fb3e"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
