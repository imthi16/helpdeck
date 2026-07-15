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

### Runtime cutover (PR: `feat/5.2a-tenant-session-cutover`)

The FastAPI process and the arq worker now serve all tenant data as `helpdeck_app`:

- **Two lanes.** *Tenant lane:* documents, conversations, chat persistence, widget
  chat/feedback, internal search, retrieval, and agent escalation all run inside
  `tenant_session(org_id)` (transaction-owning; no inner commits — helpers `flush()`
  where a generated id is needed before block exit). *Identity lane:* auth, onboarding,
  and widget key→org resolution use plain `app_session_factory` sessions — these queries
  run before a tenant is known, so they keep explicit scoping instead of RLS. A new
  migration (`677bd6a813f4`) grants the app role CRUD on `users`/`organizations`/
  `memberships` (no RLS there — see design decision above).
- **Session-factory contract.** `SessionFactory` (in `app/core/db.py`) is anything
  callable returning an `AsyncSession` context. `tenant_sessionmaker(org_id)` binds the
  tenant once for helpers that open their own short sessions (chat stream, retriever,
  agent nodes); `transactional_sessionmaker(base)` gives scripts/tests the same
  owns-the-transaction contract over the superuser engine. The SSE chat stream keeps its
  multiple-short-sessions shape — one transaction across the stream would pin a pooled
  connection for the LLM's whole lifetime.
- **Worker.** `enqueue_ingest` now carries `org_id`; `ingest_document` runs the pipeline
  under `tenant_worker_session`, which sets `app.current_tenant` *session*-scoped (the
  pipeline commits between status transitions) and clears it before the connection
  returns to the pool.
- **Known deviation:** the LangGraph checkpointer (`AsyncPostgresSaver`) still connects
  via the superuser DSN. Its tables are created lazily by `saver.setup()` at app start,
  so migration-time grants can't cover a fresh database. Checkpoint rows are keyed by
  `thread_id` (no cross-tenant enumeration surface through the app), and the cutover of
  this last path is tracked for a later hardening pass.

With this, the Phase 5.1 Verify holds end to end (`tests/test_rls.py`, including a
router-level test where the documents endpoint over the app role sees only the caller's
org), and **5.1 is checked off** in `IMPLEMENTATION_PLAN.md`.
