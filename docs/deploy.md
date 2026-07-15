# Production Deployment Runbook

Target stack (task 7.1): **Vercel** (dashboard) · **Render** (API + arq
worker, via the committed `render.yaml` blueprint) · **Neon** (Postgres +
pgvector) · **Upstash** (Redis) · **Langfuse Cloud** (tracing). Railway is a
drop-in alternative to Render (§ Alternatives).

> Everything below is prepared in-repo (Dockerfile, blueprint, CD workflow).
> The platform steps are manual one-time setup; nothing deploys until the
> secrets exist.

## 0. Prerequisites

- Accounts: Vercel, Render, Neon, Upstash (all have usable free/starter tiers).
- A hosted LLM key: **prod cannot run Ollama** on starter-size boxes, so set
  `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) plus matching `LLM_*_MODEL`s and
  a hosted `EMBEDDING_MODEL`.

## 1. Neon (Postgres + pgvector)

1. Create a Neon project (Postgres 17). In the SQL editor:
   `CREATE EXTENSION IF NOT EXISTS vector;`
2. **The `helpdeck_app` role gotcha:** Neon has no superuser. The RLS
   migration creates `helpdeck_app` as `NOLOGIN` (your Neon owner role has
   `CREATEROLE`, which is enough), but its login credential is provisioned
   out of band — run once in the SQL editor:

   ```sql
   ALTER ROLE helpdeck_app LOGIN PASSWORD '<strong-password>';
   ```

   (Run this **after** the first deploy's migrations have created the role,
   or create the role yourself first with the same statement plus
   `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`.)
3. You need **two** connection strings (both `postgresql+asyncpg://...`):
   - `DATABASE_URL` — the Neon owner role (migrations, seed).
   - `APP_DATABASE_URL` — `helpdeck_app` (serves all requests; RLS enforced).

## 2. Upstash (Redis)

Create a Redis database and copy the **TLS** URL (`rediss://...`) into
`REDIS_URL`. Both the response cache and the arq queue use it; arq speaks the
plain Redis protocol over TLS, which Upstash supports natively.

## 3. Embeddings in prod (one-way door)

`chunks.embedding` has a fixed dimension (768 for the dev default
`ollama/nomic-embed-text`). A hosted embedding model with a different width
(e.g. `text-embedding-3-small`, 1536) requires the re-dimension migration
path **before** ingesting anything (see
`alembic/versions/8bb42227bfe1_re_dimension_chunk_embeddings_*.py`). Pick the
prod embedding model once, set `EMBEDDING_MODEL`/`EMBEDDING_DIMS`, and don't
change it without re-embedding the corpus.

## 4. Render (API + worker)

1. New → Blueprint → connect the repo. Render reads `render.yaml` and creates
   `helpdeck-api` (web, health check `/health`) and `helpdeck-worker`
   (background arq worker), both from `apps/api/Dockerfile`.
2. Fill the `helpdeck-secrets` env group (every `sync: false` key).
3. Migrations run automatically before each deploy goes live
   (`preDeployCommand: uv run alembic upgrade head`).
4. Seed the demo org once from a Render shell:
   `uv run --no-sync python -m scripts.seed_widget`
5. Copy the deploy hook URLs (Settings → Deploy Hook) into the GitHub secrets
   `RENDER_DEPLOY_HOOK_API` and `RENDER_DEPLOY_HOOK_WORKER` — that arms the
   CD workflow (`.github/workflows/deploy.yml`, task 7.2).

## 5. Vercel (dashboard)

1. Import the repo; set **Root Directory = `apps/web`** (the committed
   `apps/web/vercel.json` pins the framework).
2. Environment variables: `NEXT_PUBLIC_API_URL=https://<helpdeck-api>.onrender.com`.
3. PR preview deploys come free with the Git integration — no workflow code.
4. Back on Render, set `ALLOWED_ORIGINS` and `WEB_BASE_URL` to the Vercel
   production URL, and `COOKIE_SECURE=true` is already set by the blueprint.

## 6. Widget hosting

`apps/widget/dist/helpdeck.js` is a static file. Two options:

- Serve it from the dashboard: copy the built file into `apps/web/public/`
  during CI and embed with
  `<script src="https://<vercel-domain>/helpdeck.js" data-public-key="pk_..." data-api-url="https://<api-domain>" defer>`.
- Or publish `dist/` to any static host/CDN. The iframe app ships next to the
  loader (`dist/app/`), so keep the directory structure.

## 7. Smoke checklist (after first deploy)

- [ ] `GET https://<api>/health` → `{"status": "ok"}`
- [ ] Signup on the Vercel dashboard → lands in onboarding
- [ ] Upload a doc → status reaches `ready` (worker + Redis + DB all good)
- [ ] Playground question → streamed, cited answer (hosted LLM good)
- [ ] Widget on a static test page against the prod API → cited answer
- [ ] Langfuse shows the `chat.turn` trace

## Cost table (monthly, US pricing, 2026)

| Service | Plan | Cost |
|---|---|---|
| Vercel | Hobby | $0 |
| Render web (API) | Starter | $7 |
| Render worker | Starter | $7 |
| Neon Postgres | Free → Launch | $0–19 |
| Upstash Redis | Free tier | $0 |
| Langfuse Cloud | Hobby | $0 |
| Hosted LLM (Anthropic/OpenAI) | usage | ~$5–20 at demo traffic |
| **Total** | | **~$19–55/mo** |

The plan's "$15–40/mo" assumed free inference; be honest that production
inference is a real (if small) cost — local Ollama is a dev/self-host story,
not a Render-starter story.

## Alternatives

- **Railway** instead of Render: create two services from the same
  `apps/api/Dockerfile` (one with the arq start command), add the same env
  vars, and use `railway up`/deploy hooks in CD. Railway has no committed
  multi-service manifest equivalent to `render.yaml`, which is why Render is
  the primary documented path.
- **Self-host**: `infra/docker-compose.yml` already runs the full stack
  (Postgres+pgvector, Redis, Ollama) — add the two `apps/api` containers and
  a reverse proxy, and you have the on-prem story with free local inference.
