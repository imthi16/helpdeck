"""api keys

Revision ID: fb3542d2ad85
Revises: 0ba1ec52cd01
Create Date: 2026-07-25 15:23:28.092988

Replaces the single immutable ``organizations.public_key`` with a managed
``api_keys`` table: named, scoped, revocable keys with usage tracking. Every
org's existing widget key is carried over verbatim, so snippets already embedded
in customer pages keep working, and then the column is dropped so there is one
source of truth.

No RLS on this table: the widget resolves a key to an org before any tenant
context exists (same rationale as ``users``/``memberships``/``invitations``), so
it is read through the identity session and is not granted to ``helpdeck_app``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb3542d2ad85"
down_revision: str | Sequence[str] | None = "0ba1ec52cd01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.Enum("public", "secret", name="api_key_kind"), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=128), nullable=True),
        sa.Column("scopes", sa.ARRAY(sa.String(length=64)), server_default="{}", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_keys_key_hash"), "api_keys", ["key_hash"], unique=True)
    op.create_index(op.f("ix_api_keys_org_id"), "api_keys", ["org_id"], unique=False)

    # Carry every org's current widget key over unchanged. sha256() is a Postgres
    # builtin (11+), so this needs no pgcrypto extension.
    op.execute(
        """
        INSERT INTO api_keys (
            id, org_id, name, kind, prefix, key_hash, value, scopes,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            o.id,
            'Widget key',
            'public',
            left(o.public_key, 11),
            encode(sha256(o.public_key::bytea), 'hex'),
            o.public_key,
            ARRAY['widget:chat']::varchar(64)[],
            now(),
            now()
        FROM organizations o
        """
    )

    op.drop_constraint(op.f("uq_organizations_public_key"), "organizations", type_="unique")
    op.drop_column("organizations", "public_key")


def downgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("public_key", sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    )
    # Restore each org's key from its (most recent active) public row.
    op.execute(
        """
        UPDATE organizations o
        SET public_key = k.value
        FROM (
            SELECT DISTINCT ON (org_id) org_id, value
            FROM api_keys
            WHERE kind = 'public' AND value IS NOT NULL
            ORDER BY org_id, revoked_at NULLS FIRST, created_at DESC
        ) k
        WHERE k.org_id = o.id
        """
    )
    # Orgs created after the upgrade may have no public row; give them a fresh key
    # so the NOT NULL + UNIQUE constraints can be restored.
    op.execute(
        "UPDATE organizations "
        "SET public_key = 'pk_' || replace(gen_random_uuid()::text, '-', '') "
        "WHERE public_key IS NULL"
    )
    op.alter_column("organizations", "public_key", nullable=False)
    op.create_unique_constraint(
        op.f("uq_organizations_public_key"), "organizations", ["public_key"]
    )

    op.drop_index(op.f("ix_api_keys_org_id"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_key_hash"), table_name="api_keys")
    op.drop_table("api_keys")
    postgresql.ENUM(name="api_key_kind").drop(op.get_bind(), checkfirst=True)
