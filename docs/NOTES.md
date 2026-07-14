# Engineering notes

## Phase 5.1 — Row-Level Security (RLS)

### What landed (this PR: `feat/phase-5.1-rls`)

- **`helpdeck_app` role** — a non-superuser, `NOBYPASSRLS` login role the app is meant
  to serve requests as. Created idempotently in the migration.
- **FORCE RLS + tenant-isolation policies** on the tenant *data* tables (`documents`,
  `chunks`, `conversations`, `messages`, `escalations`) using
  `org_id = current_setting('app.current_tenant', true)::uuid` for both `USING` and
  `WITH CHECK`.
- **`tenant_session(org_id)`** in `app/core/db.py` — an app-role session that sets
  `app.current_tenant` transaction-locally, plus an `app_engine`/`app_session_factory`.
- **Isolation tests** (`tests/test_rls.py`) — the Phase 5.1 Verify: an unscoped
  `SELECT * FROM documents` through the app role only returns the current tenant's rows;
  no tenant set fails closed (zero rows); the app role can't bypass RLS or `SET ROLE`
  to the owner; `WITH CHECK` blocks cross-tenant writes; a child row cannot reference a
  parent in another tenant.

Migrations, `scripts/seed*.py`, and the test suite connect as the superuser `helpdeck`
(which has `BYPASSRLS`), so they are unaffected by RLS — this is intentional.

### Review fixes (PR #2)

- **No fixed app-role password in the migration.** The role is created only if it does
  not already exist, and its password is derived from the deployment secret
  (`APP_DATABASE_URL`) rather than a hardcoded default — so prod never ships a known
  credential, and a role provisioned out of band is left untouched.
- **Fail-closed on pooled connections.** The policy predicate uses
  `NULLIF(current_setting('app.current_tenant', true), '')::uuid`: a pooled connection
  whose tenant was only ever set transaction-locally reverts to `''` (not `NULL`) after
  the txn, and a bare `''::uuid` would *raise* instead of matching zero rows. Coercing
  `'' -> NULL` keeps an unset tenant matching nothing.
- **Child rows tied to a same-tenant parent.** FK referential-integrity checks bypass
  RLS, so `WITH CHECK` on `org_id` alone let an app-role insert scoped to tenant A point
  a child (`org_id=A`) at tenant B's parent. `chunks`/`messages`/`escalations` now carry
  composite `(fk_col, org_id)` FKs to `documents(id, org_id)` / `conversations(id, org_id)`
  (backed by `UNIQUE (id, org_id)` on the parents), forcing the parent's `org_id` to
  equal the child's.

### Design decision: which tables get RLS

RLS is applied to the tenant **data** tables only. `users`, `memberships`, and
`organizations` are deliberately left out because authentication queries them
*cross-tenant* before any org context exists (e.g. "find this user", "which orgs is this
user in?"); forcing RLS there would break login. Those tables keep their existing
explicit `org_id` scoping in application code.

### Remaining work (follow-up PR)

The **runtime cutover** — making the FastAPI process actually connect as `helpdeck_app`
and routing every org-scoped endpoint (documents, conversations, dashboard chat, widget
chat, internal search, retrieval reads) through `tenant_session` so `app.current_tenant`
is set per request — is intentionally a separate PR. It touches the streaming chat path
(SSE + Postgres checkpointer + cache), where a missed `SET LOCAL` would fail closed and
break flows in subtle ways, so it is best reviewed and E2E-verified on its own. Until
then the DB-level FORCE RLS is in place as defense-in-depth, and the app continues to
rely on the explicit `org_id` filters already present in every tenant query.

Because of this, **Phase 5.1 is not yet checked off** in `IMPLEMENTATION_PLAN.md` — the
foundation and the isolation proof have landed; the app-role cutover is pending.
