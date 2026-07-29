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

Restart the API; the next playground or widget turn shows up under the project.

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
docker compose up -d          # http://localhost:3000
```

Then create an org + project in the UI, copy the keys, and set
`LANGFUSE_HOST=http://localhost:3000` in `.env`. Nothing in HelpDeck changes —
`LANGFUSE_HOST` is the only switch between Cloud and self-hosted, and the tracing
facade is the single place the SDK is touched.

## Conventions

- Only `app/services/tracing.py` imports the Langfuse SDK; a test
  (`tests/test_tracing.py::test_only_the_facade_imports_the_langfuse_sdk`) enforces it.
- New spans use `tracing.observation(...)`; new turn-level work uses
  `tracing.turn_trace(...)`. Both are no-ops when tracing is disabled, so callers never
  branch on whether Langfuse is configured.
- Short-lived processes (arq jobs, eval runs) should call `tracing.flush()` before
  exiting, otherwise buffered events are lost.
