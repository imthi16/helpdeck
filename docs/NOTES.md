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

### Runtime cutover (second PR — completes 5.1)

The request path now actually connects as `helpdeck_app`, so RLS is the enforcing layer
and the explicit `org_id` filters are defense-in-depth rather than the only defense.

- **Two session kinds, made explicit** (`app/routers/deps.py`):
  - *identity* — the privileged `async_session_factory`. `users`, `memberships` and
    `organizations` have no RLS and the app role has no grants on them, so auth,
    onboarding, and the widget's public-key → org lookup keep using it.
  - *tenant* — `tenant_db`, an app-role session with `app.current_tenant` set. Every
    handler touching `documents`/`chunks`/`conversations`/`messages`/`escalations` uses
    it: documents CRUD, conversations inbox, dashboard chat, widget chat + feedback,
    internal search, and all retrieval reads.
- **`SessionFactory` protocol + `TenantSessionFactory`** (`app/core/db.py`). The tenant
  factory is call-compatible with `async_sessionmaker`, so handlers keep the
  `async with db() as session:` shape; `HybridRetriever`, `compute_kb_version` and
  `AgentDependencies` depend on the protocol instead of a concrete sessionmaker.
- **Sessions are units of work.** `tenant_session` owns its transaction, so handlers no
  longer call `session.commit()` — they `flush()` when they need a generated id and let
  the block commit. Committing inside would drop `app.current_tenant` and make later
  queries in the same block fail closed. `unscoped_session_factory()` gives privileged
  callers (agent default, scripts, eval) the same commit-on-exit semantics with no
  tenant scope.
- **Streaming stays short-transactional.** The SSE path does *not* hold a transaction
  open across the stream: each persistence step (`_ensure_conversation`,
  `_persist_message`, the escalation write) is its own tenant-scoped transaction, as
  before the cutover.
- **`get_app_session_factory` is the single DI seam.** The API test suite overrides it
  with a loop-local `helpdeck_app` engine (`app_sessionmaker` in `conftest.py`), so
  documents/conversations/chat/widget/search tests all run under RLS with the
  least-privilege grants — a missing grant or an unset tenant scope fails in CI instead
  of at runtime. `tests/test_rls_runtime.py` is the wiring proof: a probe route shows
  `is_superuser = off`, the caller's `app.current_tenant`, and an unscoped
  `SELECT title FROM documents` returning only that tenant's rows.

### Still on the privileged connection (deliberate)

- **arq ingest worker.** The job payload is only `document_id`, so the worker has no org
  context until it reads the document — cutting it over means changing the enqueue
  contract to carry `org_id`. Tracked as follow-up work; the worker runs in-process,
  not on user input.
- **LangGraph Postgres checkpointer** — GitHub issue #64. Its tables are
  library-managed and not granted to the app role.
- Migrations, `scripts/seed*.py`, and test setup/teardown, as before.

## Phase 5.2 — RBAC enforcement and members management

RLS (5.1) keeps other tenants out; RBAC governs what a member *inside* the tenant may do.
Before this, roles existed in the data model but nothing read them: any member could
delete the knowledge base or rename the org.

### The permission model

`require_role(...)` in `app/routers/deps.py` is a dependency factory over
`current_membership` (the caller's membership in their active org). Reads are open to all
four roles — a viewer is read-only, not blind — so only write routes carry a role
dependency:

| Action | owner | admin | agent | viewer |
|---|---|---|---|---|
| Read documents, conversations, members, invites | ✅ | ✅ | ✅ | ✅ |
| Knowledge base writes (create/upload/reindex/delete) | ✅ | ✅ | ❌ | ❌ |
| Resolve / reply on conversations | ✅ | ✅ | ✅ | ❌ |
| Playground chat (`POST /api/v1/chat`) | ✅ | ✅ | ✅ | ❌ |
| Org settings (`/onboarding/complete`) | ✅ | ✅ | ❌ | ❌ |
| Invite / revoke / change role / remove member | ✅ | ✅ | ❌ | ❌ |
| Grant or revoke the **owner** role | ✅ | ❌ | ❌ | ❌ |

Two invariants live in `app/services/members.py`, not in the dependency, so they hold for
every caller: an org always keeps at least one owner (409 otherwise), and only an owner
moves ownership. `tests/test_rbac.py` is the 5.2 Verify — a 70-case matrix of every role
against every sensitive endpoint.

### Chat endpoint auth (a hole this task closed)

`POST /api/v1/chat` previously took `org_id` from the request body with **no session at
all**: anyone could chat against any org's knowledge base. It now requires a member
session, 403s when the body's `org_id` is not the caller's, and derives the tenant scope
from the membership rather than the request. The widget path is unchanged — it
authenticates with the org's public key.

### Invitations

`invitations` stores a SHA-256 hash of a single-use token; the raw token exists only in
the returned link. There is no mailer yet, so `POST /api/v1/members/invites` returns the
`/invite/<token>` URL for the inviter to share (built from `WEB_APP_URL`) and logs the
invite *without* the token. A real mailer drops in behind the same endpoint.

**No RLS on `invitations`**, for the same reason as `users`/`memberships`: redeeming a
token is a cross-tenant lookup made before any org context exists. It is reached through
the identity session and is not granted to the `helpdeck_app` role.

Redemption rules (`POST /auth/invites/{token}/accept`):

- signed in as the invited email → membership added;
- no account for that email → the password in the request creates it and signs in;
- an account exists but is not signed in → 403. Holding the link never bypasses an
  existing account's password.

Re-inviting the same email replaces the pending invite (rotating the token), enforced by a
partial unique index on `(org_id, email) WHERE accepted_at IS NULL`.
