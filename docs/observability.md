# Observability — Langfuse tracing

Every LLM and embedding call in HelpDeck is traced, one trace per conversation turn.
Tracing is **off by default**: with `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
unset, `app/services/tracing.py` hands out no-op spans and the app behaves exactly
as it does today (tests and CI run with tracing disabled).

## Enabling it

```bash
# .env — Langfuse Cloud free tier is enough for dev
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL
```

**Langfuse Cloud's free tier is the supported path** and what the implementation plan
prescribes. Self-hosting works too (below) but wants five containers; Cloud wants a
signup.

Restart the API — `get_langfuse()` is `@lru_cache`d, so keys are read once per process
and adding them to a running server does nothing. The next playground or widget turn
then shows up under the project, and the playground's debug panel prints that turn's
trace id so you can find it without hunting.

Ask a **fresh** question: a repeat is served from the response cache, which runs no
agent and therefore produces no trace (the debug panel shows `Trace —` for those).

## What a turn looks like

```
chat.turn                         (agent)      input: question, output: answer + citations
├── agent.router                  (span)       output: intent
├── agent.retrieve                (retriever)  output: chunk_id + document_id + score per chunk
│   └── embed_texts               (embedding)  the query embedding
├── agent.answer                  (span)       metadata: model, tokens_in/out
│   └── llm.stream                (generation) usage_details: input/output tokens
├── agent.faithfulness_judge      (evaluator)  output: confidence, metadata: threshold
│   └── llm.complete              (generation) usage_details from the provider
└── agent.escalate                (span)       output: reason        (escalated turns only)
```

Trace-level attributes are *propagated*, not just set on the root span, so Langfuse
aggregations (cost per tenant, per session) count every observation in the tree:

| Attribute    | Value                                                     |
| ------------ | --------------------------------------------------------- |
| `session_id` | `conversation_id` — a thread reads as one Langfuse session |
| `user_id`    | the widget's `user_identifier`, when the embed supplies it |
| `metadata`   | `org_id`, `conversation_id`, `channel`, `intent`, `latency_ms` |
| `tags`       | the channel (`playground` / `widget` / `api`)             |

### Tokens and cost

Generations report `usage_details`, which is what Langfuse multiplies by its model
price list to get cost. Two caveats worth knowing:

- **Streamed answers carry no provider usage payload**, so `llm.stream` counts tokens
  locally with tiktoken and labels the span
  `usage_source: "estimated (tiktoken); streamed response has no usage"`. Treat those
  numbers as an estimate, not billing truth.
- **The default OSS stack is free**, so cost renders as 0 for `ollama_chat/*` models.
  Point `LLM_*_MODEL` at a hosted provider and real cost appears without any code
  change.

## Why a trace might show no cost

Cost is not sent by HelpDeck — Langfuse computes it server-side by matching a
generation's `model` field against its model definitions and multiplying by the
reported tokens. Two things have to line up:

- **The model name must be bare.** litellm needs a routing prefix
  (`gemini/gemini-3.5-flash`); Langfuse's built-in patterns match bare names and a
  prefixed string matches nothing. `price_model_name()` in `app/services/llm.py` strips
  it for the span only, and the routed identifier is kept as `model_id` metadata. This
  is why cost works at all — before it, every generation priced as nothing.
- **Langfuse has to know the model.** Its bundled price list is only as new as the
  pinned SDK, so a recent model (`gemini-3.5-flash`, say) resolves to no price even
  with a bare name. Fix it in the project UI: **Settings → Models → new model
  definition**, match pattern `(?i)^gemini-3\.5-flash$`, with the provider's per-token
  input and output prices. A blank cost on a first run usually means this, not broken
  instrumentation.

**Streamed answers carry estimated tokens.** The answer route streams, and a streamed
response has no provider usage payload, so `llm.stream` counts with tiktoken and labels
itself `usage_source: "estimated (tiktoken); ..."`. Its cost is therefore an estimate by
construction — fine as a cost signal, not a billing reconciliation. `llm.complete` (the
router and the judge) carries provider-reported usage.

The default OSS stack costs 0 by definition: `ollama_chat/*` models are free, so a local
run traces a complete tree with a genuine zero cost.

## Scores

| Score          | Attached to        | Source                                              |
| -------------- | ------------------ | --------------------------------------------------- |
| `faithfulness` | trace              | the judge node's confidence, every turn             |
| `escalated`    | trace (boolean)    | whether the turn handed off; comment = reason       |
| `user_feedback`| trace (boolean)    | widget thumbs up/down on that answer                |
| `csat`         | session (1–5)      | the conversation rating                             |

Thumbs arrive minutes after the turn, so the assistant message stores the turn's
`trace_id` (`messages.trace_id`) and `POST /api/v1/widget/feedback` scores that trace.
Cache-served answers have no trace of their own, so their `trace_id` is null and the
thumbs score is skipped rather than misfiled. CSAT rates the whole conversation, so it
is a *session* score.

Score emission is best-effort: a Langfuse failure is logged
(`langfuse_score_failed`), never raised into the request.

## Self-hosting (the on-prem story)

Langfuse is MIT-licensed and self-hostable, which matters for the customers who cannot
send support transcripts to a third party. The minimum stack is the Langfuse web
container plus Postgres, Clickhouse, Redis and S3-compatible storage:

```bash
git clone https://github.com/langfuse/langfuse
cd langfuse
# Langfuse's compose publishes its web UI on 3000 — which apps/web already uses.
# Move Langfuse rather than the dashboard: relocating apps/web would also mean
# overriding ALLOWED_ORIGINS and WEB_APP_URL for CORS and invite links.
LANGFUSE_PORT=3100 docker compose up -d          # http://localhost:3100
```

Then create an org + project in the UI, copy the keys, and set
`LANGFUSE_HOST=http://localhost:3100` in `.env`.

Budget for it: web + Postgres + ClickHouse + Redis + MinIO is a heavier footprint than
the whole HelpDeck dev stack, and ClickHouse alone wants more RAM than the rest of it. Nothing in HelpDeck changes —
`LANGFUSE_HOST` is the only switch between Cloud and self-hosted, and the tracing
facade is the single place the SDK is touched.

## Conventions

- Only `app/services/tracing.py` imports the Langfuse SDK; a test
  (`tests/test_tracing.py::test_only_the_facade_imports_the_langfuse_sdk`) enforces it.
- New spans use `tracing.observation(...)`; new turn-level work uses
  `tracing.turn_trace(...)`. Both are no-ops when tracing is disabled, so callers never
  branch on whether Langfuse is configured.
- Short-lived processes (arq jobs, eval runs) should call `tracing.flush()` before
  exiting, otherwise buffered events are lost. Both honour it now: the arq worker in
  `app/workers/main.py`'s `shutdown()`, and `eval/run_eval.py`. This matters most for
  the nightly quality sweep, whose scores are emitted best-effort — a lost one is
  indistinguishable from a night with nothing to score.
