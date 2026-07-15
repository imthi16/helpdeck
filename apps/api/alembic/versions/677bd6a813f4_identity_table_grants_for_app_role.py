"""identity table grants for app role

Revision ID: 677bd6a813f4
Revises: 8bb42227bfe1
Create Date: 2026-07-14 23:03:27.530282

Grants the non-superuser ``helpdeck_app`` role access to the identity tables
(``users``, ``organizations``, ``memberships``) so the API can stop serving any
request from the superuser connection. These tables deliberately get NO
row-level security: their core lookups (login by email, widget public-key ->
org, invite acceptance) happen before a tenant is known, so a tenant-keyed
policy would break them by definition. Isolation for identity data instead
relies on the centralized query paths in ``app/services/auth.py`` and the
router dependencies — every read is keyed by primary key, unique email, or
unique public key, never enumerated.

Trade-off accepted here: a compromised app credential can read
``users.password_hash``. The API process must verify passwords anyway, so a
separate identity role would not remove that exposure — only add a third
engine to operate.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "677bd6a813f4"
down_revision: str | Sequence[str] | None = "8bb42227bfe1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"

# DELETE is granted only on memberships (member removal); users and
# organizations are never deleted by the app role.
IDENTITY_GRANTS = (
    ("users", "SELECT, INSERT, UPDATE"),
    ("organizations", "SELECT, INSERT, UPDATE"),
    ("memberships", "SELECT, INSERT, UPDATE, DELETE"),
)


def upgrade() -> None:
    for table, privileges in IDENTITY_GRANTS:
        op.execute(f"GRANT {privileges} ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table, privileges in IDENTITY_GRANTS:
        op.execute(f"REVOKE {privileges} ON {table} FROM {APP_ROLE}")
