# Observability (Langfuse)

Every LLM call, embedding batch, and agent turn is traced with
[Langfuse](https://langfuse.com). Tracing is a **strict no-op** when the keys
are unset — no code path changes, nothing is buffered.

## Setup (Langfuse Cloud free tier)

1. Create a project at https://cloud.langfuse.com (free tier is plenty for dev).
2. Copy the keys into `.env`:

   ```bash
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=https://cloud.langfuse.com
   ```

3. Restart the API. Ask a question in the playground and open the project's
   Traces view — you should see a `chat.turn` trace per turn.

## What a turn looks like

```
chat.turn                        session = conversation_id, tags = [channel],
├── agent.router                 metadata = {org_id, conversation_id}
│   └── llm.complete             (generation: model, tokens in/out, cost)
├── agent.retrieve               output = {chunk_ids, scores}
│   └── embed_texts              (embedding batch)
├── agent.answer
│   └── llm.stream               (generation: streamed answer)
├── agent.faithfulness_judge     output = {confidence}
│   └── llm.complete
└── agent.escalate               only on the escalation path
```

- The trace id is stored on the assistant message (`messages.trace_id`) and
  returned in the SSE `done`/`debug` events, so the dashboard can deep-link
  and later feedback can attach to the right trace.
- Conversations map to Langfuse **sessions** (session id = conversation id),
  so a multi-turn conversation reads as one session of turn traces.
- Cached answers still create a lightweight `chat.turn` trace with
  `metadata.cached = true`, keeping trace counts aligned with turns.

## Scores

| Score | Target | Source |
|---|---|---|
| `user_feedback` (0/1) | the turn's trace | widget thumbs (`POST /widget/feedback`) |
| `csat` (1–5) | the session | widget rating (`POST /widget/csat`) |
| `online_faithfulness` (0–1) | the turn's trace | nightly online sampling job (task 6.5) |

## Implementation notes

- Helpers live in `apps/api/app/services/tracing.py`. The SSE handler is an
  async generator, so the root span is an **explicit object** ended in the
  stream's `finally` — context-manager spans are only used inside fully
  awaited coroutine bodies (graph nodes, gateway calls), where OTEL context
  is safe.
- Self-hosting: Langfuse ships an official Docker Compose
  (https://langfuse.com/self-hosting) — point `LANGFUSE_HOST` at your
  instance; nothing else changes. This is the on-prem story for clients who
  can't send traces to a SaaS.
