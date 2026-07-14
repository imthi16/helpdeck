# Engineering notes

## Phase 5.1 — Row-Level Security (RLS)

### What landed (this PR: `feat/phase-5.1-rls`)

- **`helpdeck_app` role** — a non-superuser, `NOBYPASSRLS` role the app serves requests
  as. The migration ensures it exists (as `NOLOGIN` when absent), normalizes its
  privilege flags every run (`NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`), and
  grants only schema `USAGE` + CRUD on the five tenant tables. Its LOGIN/password is
  provisioned out of band (see below), never by the migration.
- **FORCE RLS + tenant-isolation policies** on the tenant *data* tables (`documents`,
  `chunks`, `conversations`, `messages`, `escalations`) using
  `org_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid` for both
  `USING` and `WITH CHECK`.
- **`tenant_session(org_id)`** in `app/core/db.py` — an app-role session that owns a
  single transaction (`session.begin()`) and sets `app.current_tenant` transaction-local
  for its whole lifetime, plus an `app_engine`/`app_session_factory`.
- **Isolation tests** (`tests/test_rls.py`) — the Phase 5.1 Verify: an unscoped
  `SELECT * FROM documents` through the app role only returns the current tenant's rows;
  no tenant set fails closed (zero rows); the app role can't bypass RLS or `SET ROLE`
  to the owner; `WITH CHECK` blocks cross-tenant writes; a child row cannot reference a
  parent in another tenant.

Migrations, `scripts/seed*.py`, and the test suite connect as the superuser `helpdeck`
(which has `BYPASSRLS`), so they are unaffected by RLS — this is intentional.

### Review fixes (PR #2)

- **No credentials in the migration.** The migration never sets a LOGIN or password, so
  `alembic upgrade head --sql` can never leak a secret into migration previews/CI logs.
  The login/password is provisioned out of band: `infra/init/01-app-role.sql` (dev
  docker-compose), a CI step + a `conftest` `app_login` fixture (tests), and deployment
  secret management (prod). If the role is absent the migration creates it `NOLOGIN`, so
  there is never a usable default credential.
- **Existing role not trusted.** `ALTER ROLE helpdeck_app NOSUPERUSER NOBYPASSRLS
  NOCREATEDB NOCREATEROLE` runs unconditionally, so a role someone provisioned with
  `SUPERUSER`/`BYPASSRLS` cannot silently defeat RLS.
- **Least-privilege grants.** Only schema `USAGE` and CRUD on the five tenant tables —
  no `GRANT ... ON ALL TABLES` and no `ALTER DEFAULT PRIVILEGES`, so `alembic_version`,
  identity tables, and any future table (e.g. an append-only audit log) stay out of the
  app credential's reach.
- **Tenant scope survives the whole block.** `tenant_session` owns its transaction via
  `session.begin()`, so the transaction-local `app.current_tenant` stays set for every
  query. Callers must not commit inside the block (doing so would drop the setting and
  make later queries fail closed); the block commits on clean exit.
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
