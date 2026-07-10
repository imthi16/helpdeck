# HelpDeck — Implementation Plan

> Location in repo: `docs/IMPLEMENTATION_PLAN.md`
> Worked by Claude Code, top to bottom, one task at a time.
> A task is complete ONLY when its Verify step passes → mark `[x]` and commit.
> Do not begin a phase until the previous phase's **Exit criteria** all pass.

## Status

- **Current phase:** 3 (complete) → 4 next
- **Last updated:** 2026-07-10
- **Blockers:** none

---

## Phase 0 — Scaffold & CI (Week 1)

**Goal:** empty but runnable monorepo with green CI. No features.

- [x] **0.1 Monorepo skeleton.** Create `apps/api`, `apps/web`, `apps/widget`, `eval`, `infra`, `docs`, root `README.md` (one-paragraph pitch + "Status: pre-alpha scaffold"), `.gitignore`, `LICENSE` (MIT).
      *Verify:* tree matches the layout in `CLAUDE.md`.
- [x] **0.2 Docker Compose dev infra.** `infra/docker-compose.yml` with `postgres:17` + pgvector extension (use `pgvector/pgvector:pg17` image) and `redis:7`. Healthchecks on both. Named volumes.
      *Verify:* `docker compose up -d` → both containers healthy; `psql -c "CREATE EXTENSION IF NOT EXISTS vector;"` succeeds.
- [x] **0.3 FastAPI skeleton.** `apps/api` with `uv init`; `app/main.py` app factory; `app/core/config.py` (pydantic-settings reading `.env`); `app/core/db.py` (async SQLAlchemy engine/session); structured JSON logging; `GET /health` returning `{status, version}`.
      *Verify:* `uv run uvicorn app.main:app --reload` → `curl localhost:8000/health` returns 200.
- [x] **0.4 Alembic wired.** `alembic init`, async env.py, empty baseline migration.
      *Verify:* `uv run alembic upgrade head` runs clean against Compose Postgres.
- [x] **0.5 Next.js dashboard skeleton.** `apps/web`: Next.js 16, TypeScript strict, Tailwind, shadcn/ui initialized, placeholder landing page, `lib/api.ts` fetch wrapper pointing at `NEXT_PUBLIC_API_URL`.
      *Verify:* `pnpm dev` renders; `pnpm lint && pnpm typecheck && pnpm build` pass.
- [x] **0.6 Widget skeleton.** `apps/widget`: Vite + Preact, builds a single IIFE bundle `dist/helpdeck.js` that logs "HelpDeck loaded" and reads `data-public-key` from its own `<script>` tag.
      *Verify:* `pnpm build` emits one JS file; opening `examples/demo.html` logs the message + key.
- [x] **0.7 CI pipeline.** `.github/workflows/ci.yml`: job 1 api (uv sync, ruff check, pytest), job 2 web (pnpm lint, typecheck, build), job 3 widget (build + fail if gzipped bundle > 60KB). Trigger on PR + main.
      *Verify:* CI green on a test PR.
- [x] **0.8 Env + hooks.** `.env.example` with every variable from `CLAUDE.md`; pre-commit config (ruff, ruff-format, end-of-file-fixer, check-added-large-files, detect-secrets or gitleaks).
      *Verify:* `pre-commit run --all-files` passes; committing a fake key is blocked.

**Exit criteria:** compose up healthy → API health 200 → web renders → widget builds → CI green.

---

## Phase 1 — Ingestion & hybrid retrieval (Weeks 2–3)

**Goal:** documents in, relevant chunks out. No LLM generation yet.

- [x] **1.1 Core models + migration.** SQLAlchemy models + Alembic migration for: `organizations`, `users`, `memberships (role: owner|admin|agent|viewer)`, `documents (source_type: pdf|url|text, status: pending|processing|ready|failed)`, `chunks (content, embedding vector(1536), content_tsv tsvector, metadata jsonb, token_count)`. Indexes: HNSW on `chunks.embedding` (cosine), GIN on `content_tsv`, composite `(org_id, document_id)`. `content_tsv` maintained by trigger or generated column.
      *Verify:* migration up/down clean; index presence asserted in a test.
- [x] **1.2 Extractors.** `app/services/ingestion/extractors.py`: PDF via `pypdf`, URL via `trafilatura` (strip nav/boilerplate), raw text/markdown passthrough. Return text + metadata (title, page numbers/headings).
      *Verify:* unit tests with a fixture PDF, a saved HTML page, and a .md file.
- [x] **1.3 Chunker.** Heading-aware recursive splitter: target 500–800 tokens, 10–15% overlap, never split mid-sentence, carry heading path into `metadata`. Pure function.
      *Verify:* unit tests — sizes within bounds, overlap correct, headings preserved.
- [x] **1.4 Embedding service.** `app/services/embeddings.py`: batched (≤100 texts/call), retry with exponential backoff, model from `EMBEDDING_MODEL`. All calls Langfuse-traced (no-op if keys unset).
      *Verify:* unit test with mocked provider — batching + retry behavior.
- [x] **1.5 Background jobs.** `arq` worker + Redis: `ingest_document(document_id)` job runs extract → chunk → embed → upsert, updates `documents.status`, records error message on failure.
      *Verify:* integration test — enqueue fixture PDF → status transitions to `ready`, chunks persisted with embeddings.
- [x] **1.6 Hybrid search.** `app/services/retrieval.py`: dense top-k (pgvector cosine) and full-text top-k (`ts_rank_cd` on `plainto_tsquery`) in parallel → Reciprocal Rank Fusion (k=60) → top-N with scores.
      *Verify:* unit test for RRF math; integration test — a keyword-only query and a paraphrase query both surface the right chunk.
- [x] **1.7 Reranker interface.** `Reranker` protocol with `NoopReranker` (default), `CohereReranker`, selected by `RERANKER` env. Applied to fused top-50 → top-8.
      *Verify:* unit test — noop preserves order; provider called only when configured.
- [x] **1.8 Seed corpus + search endpoint.** `eval/fixtures/` with a fictional product's docs (~20 pages: FAQ, policies, how-tos) + `scripts/seed.py` creating demo org and ingesting them. Internal `POST /internal/search {org_id, query}` returning chunks + scores (dev-only, behind a flag).
      *Verify:* for 10 hand-written queries in `eval/fixtures/queries.json`, expected chunk appears in top-3 for ≥ 8.

**Exit criteria:** pytest green; seed + search demonstrably returns relevant grounded chunks.

---

## Phase 2 — LangGraph agent, guardrails, streaming (Weeks 3–4)

**Goal:** grounded, cited, streaming answers with a safe "I don't know" path.

- [x] **2.1 LLM gateway.** `app/services/llm.py`: single entry point for chat completions; `LLM_CHEAP_MODEL` / `LLM_STRONG_MODEL` routing param; provider-agnostic (thin wrapper; litellm acceptable); token + latency capture; Langfuse tracing on every call.
      *Verify:* unit test with mocked providers; no other module imports provider SDKs (enforce with a lint test).
- [x] **2.2 Conversation persistence.** Models + migration: `conversations (channel, status: open|escalated|closed, user_identifier, csat_score)`, `messages (role, content, citations jsonb, confidence, model_used, tokens_in/out, latency_ms)`, `escalations (reason, status)`.
      *Verify:* migration clean; CRUD tested.
- [x] **2.3 Agent graph.** `app/agent/graph.py` (LangGraph): nodes `router` (intent: faq|chitchat|human_request; cheap model) → `retrieve` (Phase 1 pipeline) → `answer` (STRONG grounded prompt: answer ONLY from numbered context, cite `[n]` inline, else say you don't know) → `faithfulness_judge` (cheap LLM-as-judge: every claim supported by cited chunks? score 0–1) → conditional edge: score ≥ threshold → respond; below threshold or human_request → `escalate` (create escalation row, return handoff message). Postgres checkpointer. Thresholds in config.
      *Verify:* integration tests on seed corpus — (a) in-KB question → answer contains `[n]` citations mapping to real chunks; (b) out-of-KB question ("what is your CEO's shoe size") → refusal + escalation row; (c) "let me talk to a human" → escalation.
- [x] **2.4 SSE chat endpoint.** `POST /api/v1/chat` via `sse-starlette`: events `status` (routing/retrieving), `token`, `citation`, `done` (message_id, confidence), `error`. Heartbeat ~15s, `X-Accel-Buffering: no`, graceful `asyncio.CancelledError` on disconnect. Persists the full exchange.
      *Verify:* `curl -N` shows streaming events ending in `done`; disconnect mid-stream leaves no orphaned state.
- [x] **2.5 Response cache.** Redis exact-match cache keyed on `(org_id, normalized_query, kb_version)`; TTL config; bypass flag for playground.
      *Verify:* second identical query served from cache (asserted via header/flag) with no LLM call.

**Exit criteria:** streaming grounded cited answers; unknown → refusal + escalation; all tests green.

---

## Phase 3 — Dashboard MVP (Weeks 4–6)

**Goal:** a user can sign up, upload docs, and chat in a playground. (Main Next.js learning block — keep components boring and shadcn-standard.)

- [x] **3.1 Auth API.** `POST /auth/signup` (user + org + owner membership), `/auth/login`, `/auth/refresh`, `/auth/me`. JWT access (15m) + refresh (7d) in httpOnly cookies; passlib/bcrypt hashing.
      *Verify:* pytest — signup/login/refresh flows, wrong-password and expired-token cases.
- [x] **3.2 Web auth pages + session.** `/signup`, `/login`, middleware-protected `(dashboard)` route group, `useSession` helper, logout.
      *Verify:* manual flow + Playwright smoke: signup → land on dashboard → refresh keeps session.
- [x] **3.3 App shell.** Sidebar (Knowledge Base, Playground, Conversations, Analytics-stub, Settings-stub), topbar with org name + user menu. shadcn components only.
      *Verify:* navigation works; responsive at 375px width.
- [x] **3.4 Knowledge Base manager.** Upload PDF (drag-drop), add URL, add raw text; table of documents (title, type, status auto-refreshing, chunk count, created); delete (confirm dialog) and re-index actions wired to API (`/documents` CRUD + `/documents/{id}/reindex`).
      *Verify:* Playwright — upload fixture PDF → status reaches `ready` → chunk count > 0 → delete removes it.
- [x] **3.5 Playground.** Chat UI consuming the SSE endpoint (streaming tokens, markdown, citation chips). Debug side-panel: retrieved chunks with scores, latency breakdown, model used, token cost, confidence.
      *Verify:* Playwright — ask seeded question → streamed answer with ≥1 citation → debug panel populated.
- [x] **3.6 Conversations inbox.** List with filters (status/channel/date), transcript view, escalated queue tab, "mark resolved" action, internal reply on escalations (stored on conversation).
      *Verify:* escalation from 2.3 test appears; resolving updates status.
- [x] **3.7 Onboarding wizard.** First-login flow: name org → upload first doc → ask a test question → shown embed snippet (`<script src=... data-public-key=...>` — key becomes real in Phase 5, placeholder OK).
      *Verify:* fresh signup is routed through wizard exactly once.

**Exit criteria:** Playwright E2E green — signup → upload → grounded cited answer in playground.

---

## Phase 4 — Embeddable widget (Weeks 6–7)

**Goal:** one script tag on any site = working support chat.

- [x] **4.1 Widget API surface.** `GET /api/v1/widget/config` (branding, welcome message), `POST /api/v1/widget/chat` (SSE, same contract as 2.4), `POST /api/v1/widget/feedback` (thumbs). Auth: `X-Public-Key` header → org lookup; enforce Origin allowlist per org; Redis rate limit (per key + per IP) returning 429 + `Retry-After`.
      *Verify:* pytest — wrong key 401, wrong origin 403, burst hits 429.
- [x] **4.2 Launcher + iframe shell.** `helpdeck.js` injects a launcher bubble (position/color from `data-*`), toggles an iframe pointing at `/widget-app` (a minimal route serving the chat app), passes public key + config via URL params/postMessage.
      *Verify:* `examples/demo.html` shows bubble; open/close works; host page styles never leak in (iframe isolation).
- [x] **4.3 Widget chat UI.** Streaming messages, markdown, citation chips opening a source popover (doc title + snippet), "Talk to a human" button (→ escalation + confirmation state), thumbs up/down per answer, session persisted in `localStorage` so refresh keeps the conversation.
      *Verify:* full conversation flow on demo page against local API.
- [x] **4.4 Bundle budget + polish.** Code-split so `helpdeck.js` (loader) stays tiny and the iframe app carries the weight; loader ≤ 60KB gz hard limit (CI already enforces); async/defer safe; no globals leaked except `window.HelpDeck`.
      *Verify:* CI size check passes; Lighthouse on demo page shows no blocking impact.
- [ ] **4.5 Widget E2E.** Playwright: load demo page → open widget → ask seeded question → cited streamed answer → thumbs-up recorded → out-of-KB question → escalation message.
      *Verify:* test green in CI.

**Exit criteria:** widget fully works on a plain HTML page; E2E green. **← This is the sellable MVP line.**

---

## Phase 5 — Multi-tenancy hardening, RBAC, analytics (Weeks 7–9)

**Goal:** real isolation, roles, keys, and numbers — the enterprise-signal features.

- [ ] **5.1 Row-Level Security.** Migration: enable + `FORCE ROW LEVEL SECURITY` on all tenant tables; policies `USING (org_id = current_setting('app.current_tenant')::uuid)`; create non-superuser `helpdeck_app` DB role; app connects as it. Session/transaction middleware runs `SET LOCAL app.current_tenant = :org_id`.
      *Verify:* isolation tests — org A session querying org B rows gets zero results even with a deliberately unscoped query; app role cannot bypass.
- [ ] **5.2 RBAC enforcement.** FastAPI dependency `require_role(...)`: owner (billing/delete org/keys), admin (KB, members, settings), agent (conversations/escalations), viewer (read-only). Members page in web: invite by email (token link), change role, remove.
      *Verify:* matrix test — each role against each sensitive endpoint returns expected 200/403.
- [ ] **5.3 API keys.** `api_keys` table (hashed secret, public widget key, scopes, last_used_at); settings page to create/reveal-once/revoke; widget key = the Phase 4 public key, now real; secret keys for future server-to-server use.
      *Verify:* revoked key immediately 401s; `last_used_at` updates.
- [ ] **5.4 Audit log.** Append-only `audit_logs` (no RLS; superuser-insert via SECURITY DEFINER function or dedicated writer): auth events, member/role changes, key create/revoke, document delete, settings changes. Read-only viewer in Settings for owners/admins.
      *Verify:* actions above produce rows; rows cannot be updated/deleted by app role.
- [ ] **5.5 Analytics.** Endpoints + dashboard page: conversations over time, deflection rate (resolved without escalation), escalation rate, CSAT average, top unanswered questions (low-confidence/escalated queries clustered by similarity). Recharts/Tremor cards + charts. Consider a nightly rollup table if queries get slow.
      *Verify:* seeded + test traffic renders correct numbers (assert against fixtures).
- [ ] **5.6 CSAT + feedback loop.** Widget asks 1–5 rating when a conversation is closed/idle; stored on conversation; thumbs feedback (4.1) surfaced on messages in the inbox transcript.
      *Verify:* rating flows into analytics; thumbs visible in inbox.

**Exit criteria:** isolation matrix green; two demo orgs coexist with zero leakage; analytics live.

---

## Phase 6 — Observability & evaluation (Weeks 9–10)

**Goal:** provable quality — the portfolio's strongest signal. (Langfuse + RAGAS learning block.)

- [ ] **6.1 Langfuse everywhere.** Trace per conversation turn: spans for router, retrieval (with chunk IDs + scores), answer, judge; cost + tokens per span; `org_id`/`conversation_id` as metadata; user feedback (thumbs, CSAT) attached as Langfuse scores. Use Langfuse Cloud free tier for dev (self-host option documented in `docs/` for the on-prem story).
      *Verify:* one playground conversation shows a complete, costed trace tree in Langfuse.
- [ ] **6.2 Golden dataset.** `eval/golden.jsonl`: 100–200 items over the seed corpus — `{question, ground_truth, expected_doc_ids}`; include ~15% deliberately unanswerable questions (expected: refusal).
      *Verify:* schema-checked by a loader test; reviewed by hand.
- [ ] **6.3 RAGAS runner.** `eval/run_eval.py`: runs the full pipeline per item; computes faithfulness, answer_relevancy, context_precision, context_recall; refusal-accuracy on the unanswerable subset; writes JSON report + row into `eval_runs` table; prints a summary table.
      *Verify:* full run completes on the golden set; report saved.
- [ ] **6.4 CI eval gate.** CI job (on PRs touching `app/agent`, `app/services/retrieval*`, prompts, or `eval/`): run eval on a 30-item fast subset; FAIL if faithfulness < 0.85 or context_recall < 0.70 or refusal-accuracy < 0.90. Full set runs nightly via scheduled workflow.
      *Verify:* a deliberately weakened prompt (test branch) fails the gate; revert passes.
- [ ] **6.5 Online sampling.** Nightly arq job: sample 5–10% of the day's production conversations → judge-based faithfulness scoring → results to Langfuse scores + `eval_runs`; alert (log/webhook) if 7-day faithfulness drops > 5 points.
      *Verify:* job runs against seeded traffic; scores visible.
- [ ] **6.6 Surface the scores.** Playground debug panel shows per-answer faithfulness; Analytics gets a "Quality" card (latest eval metrics + trend).
      *Verify:* visible and correct against latest `eval_runs`.

**Exit criteria:** CI gate demonstrably blocks regressions; Langfuse traces complete; quality metrics visible in-product.

---

## Phase 7 — Deploy, polish, portfolio (Weeks 10–12)

**Goal:** live demo + a repo that converts viewers into clients.

- [ ] **7.1 Production deploy.** Web → Vercel; API + arq worker → Railway (or Render); DB → Neon (pgvector enabled); Redis → Upstash. Secrets via platform env. Run migrations on deploy. Document every step in `docs/deploy.md` with the ~$15–40/mo cost table.
      *Verify:* live URLs respond; widget on a static test page works against prod API.
- [ ] **7.2 CD pipeline.** GitHub Actions: on merge to main → tests + eval gate → build → deploy api/web; Vercel preview deploys on PRs.
      *Verify:* a trivial PR flows through preview → merge → prod automatically.
- [ ] **7.3 Public demo org.** Seed script for prod: fictional company "Northwind Coffee Supply" with rich docs; demo mode = read-only KB, rate-limited, auto-reset nightly. Landing page embeds the live widget against it.
      *Verify:* incognito visitor can chat with the demo instantly.
- [ ] **7.4 Landing page.** Hero (pitch + live widget), 4–6 feature cards (grounded answers, citations, guardrails, escalation, analytics, self-hostable), architecture section, honest "Status" note, GitHub link.
      *Verify:* Lighthouse ≥ 90 performance/accessibility; mobile clean.
- [ ] **7.5 Flagship README.** Order: one-line pitch → live demo link + 30s GIF → problem → architecture diagram (mermaid, committed as image too) → features → stack badges → quickstart (`docker compose up` path, < 10 min) → **eval results table (real RAGAS scores)** → roadmap link → honest status badge ("MVP live — WhatsApp channel in progress") → license.
      *Verify:* a fresh clone by-the-README reaches a working playground in < 10 min.
- [ ] **7.6 Project hygiene.** `ROADMAP.md` (shipped / in-progress / planned); 10–15 labeled GitHub issues (`enhancement`, `good-first-issue`, `channel:whatsapp`, ...); `docs/architecture.md`; 2–3 ADRs (pgvector-over-dedicated-DB, SSE-over-WebSockets, RLS multi-tenancy).
      *Verify:* issues + roadmap render coherently on GitHub.
- [ ] **7.7 Demo video script.** `docs/demo-script.md`, 3 minutes: (1) ingest docs live → (2) grounded answer with citations → (3) out-of-KB question refused + escalated ("it refuses to make things up") → (4) analytics + Langfuse trace + eval scores. Record separately.
      *Verify:* script table-reads at ≤ 3 minutes.

**Exit criteria:** live public demo; fresh-clone quickstart works; README + roadmap + issues portfolio-ready.

---

## Backlog (post-MVP — do NOT start without explicit instruction)

- WhatsApp channel (Meta Cloud API), then email + Slack
- Agentic actions via tool-calling: order lookup, refunds (Shopify/Stripe connectors)
- White-labeling + agency multi-workspace mode
- Self-hosted model serving option (vLLM/SGLang) for on-prem clients
- Voice channel (STT → agent → TTS)
- SSO/SAML, data-retention controls, PII redaction pass
- Fine-tuned embeddings (BGE) per large client corpus
