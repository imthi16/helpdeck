# ADR-003: Postgres Row-Level Security for multi-tenancy

**Status:** accepted (Phase 5, 2026)

## Context

Every tenant table carries `org_id`. Application-level `WHERE org_id = ...`
filtering alone is one forgotten clause away from a cross-tenant leak.
Alternatives: schema-per-tenant, database-per-tenant, or shared tables with
Row-Level Security.

## Decision

Shared tables with **FORCE ROW LEVEL SECURITY**, policies keyed on the
transaction-local `app.current_tenant` setting, served by a dedicated
non-superuser `helpdeck_app` role. Application queries **keep** explicit
`org_id` scoping as a second layer.

## Rationale

- **Fail closed in the database.** A query that forgets its `WHERE` returns
  zero rows, not another tenant's data; `FORCE` applies the policy even to
  the table owner; the app role is `NOBYPASSRLS`.
- **Right cost for the tenant count.** Schema/database-per-tenant multiplies
  migrations, connection pools, and ops per tenant — unjustified for a SaaS
  expecting many small tenants.
- **Details that made it sound:**
  - `NULLIF(current_setting('app.current_tenant', true), '')::uuid` keeps
    pooled connections fail-closed (an unset/reverted setting matches
    nothing instead of erroring).
  - Composite `(id, org_id)` foreign keys stop a child row in tenant A from
    referencing a parent in tenant B (FK checks bypass RLS).
  - Identity tables (`users`, `organizations`, `memberships`,
    `invitations`) are deliberately not RLS'd — login and invite acceptance
    happen before a tenant exists; they are protected by centralized query
    paths instead.
  - Pre-tenant lookups (widget key → org) and append-only audit writes go
    through narrow `SECURITY DEFINER` functions.

## Consequences

- Long-running jobs need session-scoped tenant settings
  (`tenant_worker_session`) because transaction-local settings die at each
  commit; connections reset the setting before returning to the pool.
- The LangGraph checkpointer still connects with the owner role (its tables
  are created lazily at startup); tracked in `docs/NOTES.md` as a deviation.
- Cross-tenant platform jobs (nightly online eval sampling) explicitly use
  the owner engine and are documented as such.
