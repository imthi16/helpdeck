# CLAUDE.md — HelpDeck

HelpDeck is a multi-tenant AI customer support agent SaaS. It answers ONLY from a
tenant's ingested knowledge base (grounded RAG), with inline source citations,
deterministic guardrails against hallucination, and automatic escalation to a
human when confidence is low. Monorepo: FastAPI backend + Next.js dashboard +
embeddable JS widget.

## Current focus

Execute `docs/IMPLEMENTATION_PLAN.md` strictly in order, one task at a time.

- Work ONLY on the next unchecked `[ ]` task unless the user says otherwise.
- A task is done ONLY when its Verify step passes. Then check it off `[x]` in the plan and commit.
- Never start a new phase before the previous phase's Exit criteria all pass.
- If a task is ambiguous or two approaches seem valid, explain both options and ask — do not decide architecture silently.

## Stack (pinned — do not swap without asking)

- Backend: Python 3.12, FastAPI (async), SQLAlchemy 2 + Alembic, Pydantic v2, `uv` for deps
- Agent: LangGraph 1.x, Postgres checkpointer; provider-agnostic LLM gateway in `app/services/llm.py`
- LLM: default free/OSS via Ollama through litellm — cheap `ollama_chat/llama3.2:3b`, strong `ollama_chat/qwen2.5:7b`; hosted Anthropic/OpenAI optional (set a key + matching `LLM_*_MODEL`). Offline stubs when neither is available.
- Data: Postgres 17 + pgvector (HNSW) + full-text `tsvector` (GIN); Redis 7 (cache, rate limits, `arq` job queue)
- Retrieval: hybrid dense + BM25-style full-text → Reciprocal Rank Fusion → optional reranker
- Embeddings: default free/OSS `ollama/nomic-embed-text` (768 dims) — pinned via `EMBEDDING_MODEL`/`EMBEDDING_DIMS`; must match the `chunks.embedding` column width
- Frontend: Next.js 16 App Router, TypeScript strict, Tailwind, shadcn/ui, `pnpm`
- Widget: Preact + Vite, single bundle ≤ 60KB gzipped, rendered in an iframe
- Streaming: SSE via `sse-starlette` (NOT WebSockets)
- Observability: Langfuse (all LLM calls traced). Eval: RAGAS golden set, gated in CI
- Infra: Docker Compose (dev); Vercel + Railway/Render + Neon (prod)

## Repository layout

```
apps/api/        FastAPI app  (app/core, app/models, app/schemas, app/routers,
                 app/services, app/agent, app/workers) + tests/ + alembic/
apps/web/        Next.js 16 dashboard (app/, components/, lib/)
apps/widget/     Embeddable widget bundle (src/, vite.config.ts)
eval/            RAGAS golden dataset + run_eval.py
infra/           docker-compose.yml, terraform/
docs/            IMPLEMENTATION_PLAN.md, architecture.md, ADRs
```

## Commands

```bash
# Infra (repo root)
docker compose -f infra/docker-compose.yml up -d      # postgres+pgvector, redis

# Backend (run inside apps/api)
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
uv run pytest -q                                      # tests
uv run ruff check . && uv run ruff format .           # lint + format
uv run arq app.workers.main.WorkerSettings            # background worker

# Web (inside apps/web)
pnpm install && pnpm dev                              # http://localhost:3000
pnpm lint && pnpm typecheck && pnpm build

# Widget (inside apps/widget)
pnpm dev
pnpm build                                            # -> dist/helpdeck.js

# E2E + eval (repo root)
pnpm exec playwright test
uv run --project apps/api --group eval python eval/run_eval.py --subset fast --gate
```

## Environment variables

Defined in `.env` (never committed). ALWAYS update `.env.example` when adding one.

`DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `OLLAMA_BASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`EMBEDDING_MODEL`, `EMBEDDING_DIMS`, `LLM_CHEAP_MODEL`, `LLM_STRONG_MODEL`, `RERANKER` (none|cohere|bge),
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `ALLOWED_ORIGINS`

## Architecture at a glance

- Chat flow: widget/playground → `POST /api/v1/chat` (SSE) → LangGraph:
  `router → retrieve (hybrid+RRF) → answer (grounded, cites [n]) → faithfulness_judge → respond | escalate`
- Ingestion: upload/crawl → extract → heading-aware chunking (500–800 tokens,
  10–15% overlap) → batched embed → upsert `chunks` (vector + tsvector) — runs as arq jobs
- Multi-tenancy: every tenant table has `org_id`; Postgres RLS (FORCE) enforces
  isolation; middleware runs `SET LOCAL app.current_tenant` per transaction
- Auth: JWT (dashboard) with RBAC roles owner/admin/agent/viewer; per-org public
  API key for the widget, locked to allowed origins + Redis rate limited

## Rules

- Run lint + typecheck + relevant tests after EVERY code change, before saying a task is done.
- Make minimal changes — do not refactor or reformat code unrelated to the task.
- One logical change per commit. Conventional commits (`feat:`, `fix:`, `test:`, `chore:`, `docs:`).
- TDD bias: new logic (chunker, RRF, guardrails, RLS, auth) ships WITH tests in the same commit.
- Schema changes ONLY via Alembic migrations — never edit the DB or models without a migration.
- Every tenant-scoped query relies on RLS AND explicit `org_id` scoping. The app NEVER connects as a superuser. Never disable RLS to "fix" a bug.
- Every LLM/embedding call goes through `app/services/llm.py` and is Langfuse-traced. No direct provider SDK calls elsewhere.
- The grounded prompt contract is inviolable: answer only from retrieved context, cite `[n]`, otherwise say you don't know and escalate. Never weaken it to make a test pass.
- Never commit secrets, `.env`, or API keys. Never log full document contents or PII.
- Never fabricate data, eval scores, or stub implementations presented as complete. If blocked, write findings to `docs/NOTES.md` and stop.
- Keep the widget bundle ≤ 60KB gzipped — check with `pnpm build` output before finishing widget tasks.
- When a library choice is not pinned above, propose it with a one-line rationale before adding it.

## Session workflow

1. Read `docs/IMPLEMENTATION_PLAN.md`; find the current phase and next unchecked task.
2. For multi-file tasks, enter plan mode first and present the plan before editing.
3. Implement → verify → check off `[x]` → commit.
4. Suggest `/clear` between phases to keep context fresh.
