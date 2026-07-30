"""eval runs org scoping

Revision ID: d7a3f81b2e64
Revises: c14046db7867
Create Date: 2026-07-30 05:20:00.000000

6.3 created eval_runs as a system table: a golden-set run scores the pipeline
against a fixture corpus, so it belongs to no tenant. 6.5 changes that — online
sampling scores *real conversations*, and an aggregate over one org's traffic is
that org's data. So the table now carries a nullable org_id and gains RLS.

Nullable, because the two kinds differ in nature rather than in degree:

- ``kind='golden'`` rows have org_id NULL. They describe the fixture corpus, are
  written by the eval CLI as the superuser, and are not tenant data.
- ``kind='online'`` rows carry the org whose conversations were sampled.

The tenant policy is the standard ``org_id = current_tenant``, which means golden
rows (org_id NULL) match no tenant and stay invisible to the app role. That is
deliberate: a tenant's Quality card (6.6) should show quality measured on *their*
traffic, not scores from a demo corpus they have never seen.

The app role gains INSERT here as well as SELECT. The nightly job runs in-process
as the app, not as the superuser the CLI uses, and RLS WITH CHECK constrains what
it can write to its own org.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7a3f81b2e64"
down_revision: str | Sequence[str] | None = "c14046db7867"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "helpdeck_app"

# Same NULLIF guard as the core RLS migration: a pooled connection whose tenant
# setting was transaction-local reverts to '' rather than NULL, and ''::uuid raises
# instead of returning zero rows. Coerce '' -> NULL so an unset tenant matches
# nothing rather than erroring.
TENANT_PREDICATE = "org_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def upgrade() -> None:
    op.add_column("eval_runs", sa.Column("org_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "eval_runs_org_id_fkey",
        "eval_runs",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # 6.6 reads "latest run for this org" and a trend over recent runs; both are
    # (org_id, created_at DESC) scans.
    op.create_index(
        "ix_eval_runs_org_id_created_at",
        "eval_runs",
        ["org_id", sa.text("created_at DESC")],
    )

    op.execute(f"GRANT INSERT ON eval_runs TO {APP_ROLE}")
    op.execute("ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eval_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON eval_runs "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON eval_runs")
    op.execute("ALTER TABLE eval_runs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eval_runs DISABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE INSERT ON eval_runs FROM {APP_ROLE}")
    op.drop_index("ix_eval_runs_org_id_created_at", table_name="eval_runs")
    op.drop_constraint("eval_runs_org_id_fkey", "eval_runs", type_="foreignkey")
    op.drop_column("eval_runs", "org_id")
