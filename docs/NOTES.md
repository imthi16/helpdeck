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

## Phase 5.3 — API keys

The widget used to authenticate with one immutable string on the org row
(`organizations.public_key`): unrotatable, unrevocable, unnamed, with no usage record. That
column is gone; `api_keys` is now the single source of truth. The migration
(`fb3542d2ad85`) carries every org's existing value over verbatim, so snippets already
embedded in customer pages keep working, and `downgrade()` restores the column from the
public rows.

### Two kinds, one lookup path

| | public (`pk_…`) | secret (`sk_…`) |
|---|---|---|
| Purpose | widget, in a browser | future server-to-server |
| Stored | plaintext `value` **and** `key_hash` | `key_hash` only, `value` NULL |
| Retrievable later | yes — the embed snippet needs it | no, shown once at creation |

Authentication always resolves by SHA-256 hash (`resolve_key`), so no code path compares a
raw secret. Public keys keep their plaintext because they are *not* secrets — they ship
inside page HTML — and the dashboard has to render them; treating them as secret would buy
nothing and break the snippet.

**Secret keys are inert.** Nothing accepts one yet — they exist so the create/reveal-once/
revoke flow and storage are in place for a later server-to-server surface. The UI says so
too, to avoid implying working auth.

**No RLS on `api_keys`**, same rationale as `users`/`memberships`/`invitations`: the widget
resolves a key to an org before any tenant context exists. Identity session only, not
granted to `helpdeck_app`.

### Revocation and usage

`resolve_key` filters on `revoked_at IS NULL` and nothing caches it, so a revoked key 401s
on the very next request — that is the 5.3 Verify (`tests/test_api_keys.py`). Revoked keys
are kept and shown as revoked rather than deleted, so the history stays legible. Revoking
the *widget* key is the rotation path: `GET /api/v1/keys/widget` issues a fresh one when
none is active, so a site owner can rotate without an outage window.

`last_used_at` is written at most once per minute per key (`LAST_USED_THROTTLE`) — without
the throttle every widget request would carry a write.

### Who may do what

Keys are a security surface, so unlike the members roster even *reading* them is admin+:
agent and viewer get 403 and the Settings tab is hidden for them. Issuing or revoking a
**secret** key is owner-only; admins manage public keys.

## Phase 5.4 — Audit log

Roles and keys were enforced (5.2/5.3) but nothing recorded *who did what*. `audit_logs` now
captures auth events, member/invite/role changes, key create/revoke, document deletion, and
settings updates.

### Append-only is a privilege, not a convention

The migration grants the app role exactly `SELECT, INSERT` on `audit_logs` and nothing else,
so the application has no privilege to rewrite or erase history — `UPDATE`/`DELETE` fail at
the database, which is what `tests/test_audit_log.py` asserts. The superuser keeps full
rights so `ON DELETE CASCADE` from `organizations` and test cleanup still work; the
alternative (a trigger rejecting every delete) would have broken org deletion.

The writer (`app/services/audit.py`) opens its own app-role session and is **best-effort**:
a failed audit write logs a warning instead of failing the action it describes. Losing a row
is bad; failing an invite the user already completed is worse, and the failure is visible in
the logs either way.

No RLS, like the other identity-adjacent tables — a login event exists before any org context
does (`org_id` is nullable for exactly that reason). Reads filter by `org_id` explicitly and
are admin+ only, keyset paginated on `created_at`.

### What is never recorded

Only ids, emails, role names, key kinds/names, and document titles. Not secrets, not tokens,
not document contents. Notably the audit metadata does **not** include an API key's `prefix`,
even though the dashboard shows it: the trail is permanent, and the key id already identifies
the row precisely.

Deleting a user sets `actor_user_id` to NULL but `actor_email` is denormalised onto the row,
so removing an account cannot quietly erase who performed an action.

## Phase 5.5 — Analytics

`GET /api/v1/analytics/overview?days=N` over the **tenant session**, so the numbers are the
caller's org by construction rather than by remembering a filter. Read-only, so every role
may see it.

- **deflection** = conversations with an assistant message and no escalation ÷ total;
  **escalation** = conversations with ≥1 escalation ÷ total. Both are returned as 0–1
  fractions; the dashboard formats the percentage.
- **conversations over time** is zero-filled with `generate_series`, so a quiet day is a zero
  bar rather than a gap in the axis.
- **top unanswered**: user questions from turns that escalated or came back below
  `LOW_CONFIDENCE`, greedily clustered by cosine similarity (≥ 0.85) over at most the last
  200 questions. Offline the embedding provider is a bag-of-tokens hash, so reworded
  duplicates still group by shared vocabulary — the clustering is meaningful in both modes.
- **No rollup table.** Live queries over one org are a few index scans; issue #63 tracks the
  nightly rollup for when that stops being true.

`tests/test_analytics_api.py` seeds an exact shape (5 conversations, 2 escalated, CSAT 4 and
5, one question asked twice) and asserts exact values — 60% deflection, 40% escalation, CSAT
4.5, the duplicate clustered into one row with count 2 — rather than "looks plausible".

### Charts

Recharts (dashboard only — the widget's 60KB budget is untouched), built against the
`dataviz` skill: one series so no legend, the validated blue for each mode
(`#2a78d6` light / `#3987e5` dark, both PASS the palette validator against their surfaces),
recessive hairline grid, 24px max bar with a 4px rounded data-end, only the busiest day
directly labelled, tooltip text in ink rather than the series colour, and a "Show data"
table view so nothing is gated behind reading a chart.

## Phase 5.6 — CSAT and the feedback loop

`conversations.csat_score` has existed since Phase 2 and nothing ever wrote it;
`messages.feedback` was written by the widget's thumbs but never shown to the team. Both are
now closed loops.

- `POST /api/v1/widget/csat` (public-key auth, tenant session) records a 1–5 score. It is
  **write-once**: re-rating would silently move the org's average, and the widget only asks
  once anyway. Out of range → 422, another org's conversation → 404, second rating → 409.
- **When the widget asks.** Two triggers, at most one ask per conversation (remembered in
  `localStorage`): 60s idle after an answered turn, and — more usefully — **closing the
  panel**. The close case has to show the prompt *instead of* closing; rendering it as the
  panel disappears would ask a question nobody can see, so the first close on an answered,
  unrated conversation swaps in the rating row and the next close goes through. "Not now"
  latches the same "already asked" flag without submitting a score.
- **The inbox** shows 👍/👎 per assistant message and `Rated n/5` beside the conversation
  status, so the team sees what visitors thought without leaving the transcript.
- `tests/test_csat.py` is the 5.6 Verify: a rating persists *and* shows up in the analytics
  CSAT average — the "rating flows into analytics" requirement — plus the guards above and
  the transcript exposing `feedback`.

The widget loader is unchanged at ~1 kB gzipped (the rating UI lives in the iframe app), so
the 60KB budget is untouched.

## Phase 6.1 — Langfuse everywhere

`app/services/tracing.py` was a 19-line client factory; it is now the tracing **facade** and
the only module allowed to import the Langfuse SDK (enforced by
`tests/test_tracing.py::test_only_the_facade_imports_the_langfuse_sdk`). Callers use
`observation()`, `turn_trace()`, `score_trace()` / `score_session()`, all of which hand out
no-ops when the keys are unset, so nothing branches on whether tracing is configured.

- **A latent bug this closed.** `llm.py` and `embeddings.py` called
  `langfuse.start_as_current_generation(...)`, which does not exist in the pinned SDK
  (langfuse 4.13.2 — it is `start_as_current_observation(as_type="generation")`). Tracing was
  never exercised because no test sets keys, so the first request with `LANGFUSE_*` set would
  have raised `AttributeError` inside the gateway. Both now go through the facade.
- **One trace per turn.** `run_chat_stream` opens `chat.turn` and the graph's node spans
  (`agent.router`, `agent.retrieve`, `agent.answer`, `agent.faithfulness_judge`,
  `agent.escalate`) nest under it. `org_id` / `conversation_id` are *propagated*, not merely
  set on the root: Langfuse aggregations only count observations that carry the attribute, so
  per-tenant cost would otherwise miss every child span. The conversation doubles as the
  Langfuse `session_id`, so a thread reads as one session.
- **Retrieval is the audit trail.** `agent.retrieve` records chunk id, document id and fused
  score per chunk — exactly what the model was allowed to see for that answer.
- **Streamed answers have no provider usage payload**, so `llm.stream` counts tokens with
  tiktoken and labels the span `usage_source: "estimated (tiktoken); ..."` rather than passing
  an estimate off as billing truth. Local OSS models cost 0 by definition; point `LLM_*_MODEL`
  at a hosted provider and real cost appears with no code change.
- **Feedback needs a trace id later than the turn.** Thumbs arrive minutes after the answer,
  so `messages.trace_id` (migration `c41d0a9e7b52`) stores the turn's trace and
  `/widget/feedback` scores it. Cache-served answers have no trace of their own → null
  `trace_id` and the score is skipped rather than misfiled. CSAT rates the whole conversation,
  so it is a *session* score, not a trace score.
- Score emission is best-effort (`langfuse_score_failed` logged, never raised): telemetry must
  not fail a request.

`docs/observability.md` documents the trace shape, the score table, and self-hosting Langfuse
for the on-prem story.

**Verify status:** the automated half is done — `tests/test_tracing.py` asserts the whole tree
(node spans, chunk ids + scores, `usage_details` on both generations) against a recording fake,
and `test_chat_endpoint.py::test_turn_is_traced_and_trace_id_persisted_on_the_message` covers
the playground path end to end. The plan's *"shows a complete, costed trace tree in Langfuse"*
needs real `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (Cloud free tier) — no keys are
available in this environment, so that eyeball check is outstanding and 6.1 stays unchecked
until someone runs one playground turn against a real project.

## Phase 6.2 — Golden dataset

`eval/golden.jsonl`: **107 hand-written items** over the eight documents in
`eval/fixtures/corpus` — 91 answerable, 16 unanswerable (15%). Schema per line:

```json
{"id": "g001", "question": "...", "ground_truth": "...",
 "expected_doc_ids": ["returns"], "answerable": true, "tags": ["fast"]}
```

- **`expected_doc_ids` are corpus file stems** (`returns`, `descaling`, ...) because
  `seed_corpus` titles each document `path.stem`. That keeps the file stable across re-seeds,
  where document UUIDs change every time; 6.3 resolves slug → document by title.
- **Coverage:** every document gets 10–15 questions (some items cite two docs, e.g. "does
  skipping descaling void my warranty" → `descaling` + `warranty`). A document nobody asks
  about is a retrieval blind spot the eval cannot see, so a test asserts full coverage.
- **Unanswerable items are genuinely absent from the corpus** — prices, machine dimensions,
  boiler wattage, voltage, colorways, phone support, a retail store, wholesale, barista
  classes, data retention. Deliberately *not* used: "do you ship to Australia", which the
  shipping policy does answer ("US, Canada, EU only"), so refusing it would be wrong.
  Their `ground_truth` states the expected refusal and `expected_doc_ids` is empty.
- **`tags: ["fast"]` marks the 30-item CI subset** for 6.4: 25 answerable spread across all
  eight docs plus 5 refusals, so the gate can measure faithfulness, recall *and* refusal
  accuracy on the subset.
- **Not ported from `main`.** `main` also ships a `golden.jsonl`, but its corpus has since
  grown to 17 documents; its items reference docs that do not exist on this branch, so
  reusing it would have meant expected answers with no source text behind them.

**Verify:** `apps/api/tests/test_golden_dataset.py` is the loader + schema check (parseable
JSONL, unique ids and questions, doc slugs that exist, refusals with no doc ids, 10–20%
unanswerable share, fast subset usable as a gate). The hand review was backed by a throwaway
audit that scored every answerable ground truth's lexical overlap against its cited document
and against every other document; six paraphrase-heavy items scored below 0.75 and were each
re-read against the source text (all correct, cited doc still the best match).

## Phase 6.3 — RAGAS runner

`eval/run_eval.py` scores `eval/golden.jsonl` through **the real pipeline** —
`app.agent.runner.run_turn`, the same graph the chat endpoint serves. Nothing is stubbed,
so a regression in the router, retrieval, the grounded prompt or the gate shows up here.
Metrics live in `app/services/evaluation/`, a JSON report goes to `eval/reports/`
(gitignored), and one row goes to `eval_runs`.

- **Our own RAGAS-style metrics, not the `ragas` package.** Every LLM/embedding call has to
  go through `app/services/llm.py` to be gateway-routed and Langfuse-traced, which `ragas`
  will not do. Retrieval metrics use the golden set's `expected_doc_ids` rather than an LLM
  relevance opinion — relevance is a fact about the corpus, so a label beats a judgement.
  Report wording says *"RAGAS-style"*, never *"scored by RAGAS"*.
- **`run_turn` takes an optional `conversation_id`.** Scoring 107 items used to mean 107
  synthetic conversations and their escalations in the demo org's inbox; with no
  conversation in state the `escalate` node skips its write. A test asserts a run leaves
  zero rows behind.
- **`eval_runs` is a system table** — no `org_id`, no RLS, app role gets SELECT only,
  following the `audit_logs` precedent. 6.5 adds org scoping when it starts judging real
  per-tenant traffic.

### The judge has to be the strong model

Both bugs below were found by running against real Ollama models; the offline stub is
lexical and hides them.

1. **`parse_verdicts` was last-write-wins.** Verdicts went into a dict keyed by claim
   number, so a judge that emits two lines for one claim silently overwrote its own
   ruling. `llama3.2:3b` answers `"1: yes\n1: no"` to a single claim, and the stray `no`
   stuck. Now `setdefault` — first ruling wins, matching the prompt's "one line per claim,
   in order". This would bite with any repetitive judge, not just a small one.
2. **The entailment call ran on `LLMRoute.cheap`.** The pipeline's in-line faithfulness gate
   is cheap because a user is waiting. Nobody waits on an eval, and a judge too weak for
   the task does not add noise — it adds *bias*, all of it toward false "unsupported", which
   reads as hallucination in the report. `JUDGE_ROUTE = LLMRoute.strong`, overridable per
   call and via `--judge-route`.

Measured on the 30-item fast subset, same pipeline, same corpus, judge swapped:

| judge | faithfulness | answer_relevancy |
|---|---|---|
| `llama3.2:3b` (before) | 0.611 | 0.757 |
| `qwen2.5:7b` (after) | **1.000** | 0.783 |

The 3B judge scored *"the return window is 30 days from the date the carrier marks the
package as delivered"* — stated almost verbatim in `returns.md` — as unsupported.

**A 1.0 is only meaningful if the metric can fail**, so the judge was checked against
planted claims: grounded → 1.00, fabricated ("returns accepted for a full ninety days")
→ 0.00, mixed → 0.50. It discriminates; the 1.0 is real. Reproduce with a few lines
against `metrics.faithfulness` — it is not a unit test because it needs a live model.

`environment.judge_model` is now in every report and `eval_runs` row. Without it two runs
are not comparable, and the pre-fix reports are silently unlabelled on exactly that axis.

**Cheap is not faster here.** The strong-judge fast run took 1486s against 2028s for the
cheap-judge one: alternating 3B and 7B forces Ollama to swap weights, and keeping most
calls on one model beats the smaller model's per-token speed.

### What the numbers say about the pipeline (not the harness)

Real-model fast subset: faithfulness 1.000, context_recall 0.840, refusal_accuracy 1.000,
**false_refusal_rate 0.400**, escalation_rate 0.500. The pipeline is very conservative — it
refuses half the set, and what survives is correct. Two distinct causes, both **pipeline
issues outside 6.3's scope**, to weigh before 6.4 sets its thresholds:

- **4 of 25 answerable items retrieve nothing relevant** (recall 0.0): `g050`, `g051`
  ("my machine will not turn on", "my shots pour fast and taste weak") plus `g075`, `g088`.
  Symptom-phrased questions do not match `troubleshooting.md`'s wording — a genuine hybrid
  retrieval gap, and the strongest argument for turning on a reranker.
- **5 more are killed by the pipeline's own gate despite perfect recall** — `g019`, `g038`,
  `g064`, `g077`, `g085` came back cited and correct, scored 0.0–0.33 by the in-line
  `llama3.2:3b` judge, and escalated under `faithfulness_threshold=0.4`. That is the *same*
  weak-judge failure fixed above, still live in the serving path where the cheap route is
  the right call for latency. Worth revisiting: a better cheap judge, a lower threshold, or
  a two-stage gate.

So 6.4's gate would pass faithfulness and refusal-accuracy comfortably and fail on nothing,
but a `false_refusal_rate` this high is the real quality story and deserves its own
threshold — refusing everything trivially aces `refusal_accuracy`.

**Verify:** full 107-item run against the seeded demo org with real models, zero errors,
report written and `eval_runs` row recorded. Numbers above come from `LiteLLMProvider` +
Ollama; the offline stub run is labelled `OfflineGroundedProvider` and the summary prints a
warning that those numbers validate the harness only and are not a quality claim.

## Phase 6.4 — CI eval gate

`app/services/evaluation/gate.py` applies thresholds to a finished report; `--gate` on
`eval/run_eval.py` exits non-zero when a blocking one fails and records the verdict on
both the JSON report and the `eval_runs.passed` column. The gate is pure arithmetic over
`report.metrics`, so it is fully testable without a model (`tests/test_eval_gate.py`).

Two rules it follows, both about failing in the safe direction:

- **A missing metric fails.** An empty or mostly-errored report must not read as
  "nothing exceeded the threshold". Errored items are also excluded from the averages,
  so the CLI fails on `counts.errors` *before* the gate can bless a run that mostly
  crashed.
- **`false_refusal_rate` is advisory, not blocking.** `refusal_accuracy` alone is aced
  by refusing everything, so the counterpart is reported at a 0.25 ceiling but does not
  fail the build — 6.3 measured 0.40 on this pipeline, so blocking on it would fail
  every PR until retrieval and the serving gate improve. Promoting it is a one-line
  change (`advisory=False`), covered by a test.

Applied to 6.3's real-model fast subset, all three of the plan's thresholds pass
(faithfulness 1.000 ≥ 0.85, context_recall 0.840 ≥ 0.70, refusal_accuracy 1.000 ≥ 0.90)
and the only signal is the advisory. **The gate as specified would not have caught this
pipeline's actual defect** — worth remembering before trusting a green run.

### CI needs a hosted model *and* a local embedder

The gate is only meaningful against real models: `OfflineGroundedProvider` ignores the
system prompt entirely, so the plan's Verify — a deliberately weakened grounded prompt
failing the gate — produces *identical* stub output and passes. A stub-backed gate is
theatre. The PR job therefore fails loudly when `ANTHROPIC_API_KEY` is absent rather
than skipping: a gate that silently passes without credentials is not a gate.

**The trap: `ANTHROPIC_API_KEY` alone is not enough.** `llm.py` switches to hosted
Claude as soon as the key is set, but `embeddings.py` picks its provider independently
and **Anthropic serves no embedding model** — so embeddings fall through to
`HashingEmbeddingProvider` behind a `logger.warning`. Dense retrieval silently becomes a
bag-of-words match, `context_recall` measures the wrong thing, and the gate goes green
over a pipeline that was never exercised. Two defences:

- CI runs Ollama for **embeddings only** (`nomic-embed-text`, 274MB; it embeds 8 seed
  documents plus 30 queries, so CPU is fine). OpenAI embeddings would also work but are
  1536-dim, and `EMBEDDING_DIMS` is pinned to 768 by the `chunks.embedding` column.
- `runner.stub_providers()` names any stub in a run's environment; `--gate` refuses to
  pass when it finds one, and `format_summary` calls out the embedding case explicitly.
  `environment.embedding_provider` is recorded alongside `llm_provider` for the same
  reason `judge_model` is — an unlabelled run is an unreadable number.

Models: `anthropic/claude-haiku-4-5` cheap (router, serving gate), `anthropic/claude-opus-5`
strong (answers, eval judge) — matching the cheap/strong split CLAUDE.md pins. Roughly
**$0.25 per gated PR**: ~60 Opus calls at ~600 input tokens each with very short answers,
plus a similar number of Haiku calls. The PR job runs only when the diff touches
`app/agent`, `app/services/retrieval*`, `app/services/evaluation`, `eval/`, or the
workflow itself, so a docs-only PR pays nothing.

`.github/workflows/eval-nightly.yml` runs the full 107-item set at 03:15 UTC and keeps
its report for 30 days. It persists the `eval_runs` row (the PR gate passes `--no-db`),
though against a throwaway CI database that row lives only as long as the job — pointing
it at a persistent instance is what 6.6's Quality card will need.

**Verify:** outstanding. Gate logic, workflows, and stub guard are tested (284 tests
green), and the gate has been exercised end to end both ways — it refuses with exit 1 on
a stub run, and passes on 6.3's real-model report. The plan's weakened-prompt
demonstration needs the GitHub secret; it cannot be done against the stub, and on local
CPU models each fast-subset run is ~25 minutes.

## Gemini as a hosted provider

`GEMINI_API_KEY` now selects Gemini for both chat (`gemini/<model>` via litellm) and
embeddings. It is the only hosted provider that covers **both**, and the only one
whose embedding width can be asked for: `gemini-embedding-001` defaults to 3072 dims
but honours `outputDimensionality`, so requesting 768 keeps vectors compatible with
the `chunks.embedding` column. OpenAI's 1536 would need a migration.

`GeminiEmbeddingProvider` asserts the returned width and raises rather than letting a
mismatch reach the INSERT, where pgvector's error names no cause.

**Gemini embeddings measurably beat local nomic-embed-text** on the same corpus and
questions: context_precision 0.917 vs 0.688, answer_relevancy 0.871 vs 0.783. A
re-seed takes ~13s against ~90s. Embeddings are pure upside.

### Free-tier generation quota makes Gemini unusable for the eval

Generation quota is metered **per model per day** (`GenerateRequestsPerDayPerProject
PerModel-FreeTier`) with caps that vary by two orders of magnitude:

| model | free daily cap |
|---|---|
| `gemini-3.5-flash` | **20** |
| `gemini-3.6-flash` | **20** |
| `gemini-3.1-flash-lite` | 500 |

The 30-item fast subset needs ~60 strong-route calls and the full set ~160, so **no
capable Gemini model can serve the eval on a free key**. Only the flash-lite tier has
the headroom, and using a lite model as the judge would reintroduce precisely the
weak-judge bias 6.3 diagnosed and fixed. Both CI workflows therefore need **paid**
Gemini quota; they fail loudly rather than silently degrading.

Local dev meanwhile runs a hybrid: Gemini embeddings, Ollama generation. Provider
selection is global rather than per-route, but this combination still works because
litellm's `ollama_chat` defaults to localhost and ignores the irrelevant `api_key` —
so a hosted key does not force hosted generation.

### Three bugs the switch exposed

1. **`conftest.py` did not isolate tests from hosted providers.** It blanked
   `OLLAMA_BASE_URL` and claimed "no provider key is set", but nothing set the keys —
   true only because none existed. A key in `apps/api/.env` made the whole suite issue
   real, billed, rate-limited API calls (200s → over 600s) and silently voided the
   "deterministic, never depends on a live model server" guarantee. Now every provider
   key is blanked explicitly. An `ANTHROPIC_API_KEY` would have done the same.
2. **`score_item` guarded only `run_turn`, not the metric calls.** Scoring calls a
   model too, so it fails the same ways — a 429 on exhausted quota, a 503 under load —
   and those escaped the per-item `except` and aborted the whole run, discarding every
   item already scored. The intent ("one bad item must not void the run") now covers
   scoring as well.
3. **`mean([])` returned 0.0, so an unmeasured metric read as a failing score.** A
   subset with no unanswerable items has *undefined* refusal accuracy; reporting 0.000
   made the gate say "out-of-KB questions must be refused" about a run that asked
   none. `mean_or_none` distinguishes the two and both the summary and the gate now
   render "not measured". It still fails the gate — you cannot certify what you did
   not measure — but for an honest reason.

`LiteLLMProvider` also gained retry-with-backoff for transient upstream failures (429
and 503), preferring the provider's own `retry in Ns` hint and refusing to honour a
multi-hour one, which means an exhausted daily quota fails fast instead of sleeping.
