# HelpDeck Roadmap

Honest snapshot of what's shipped, what's being worked on, and what's planned.
Issues track the planned items — see the
[issue list](https://github.com/imthi16/helpdeck/issues).

## ✅ Shipped

- **Ingestion & hybrid retrieval** — PDF/URL/text extraction, heading-aware
  chunking, batched embeddings, pgvector + full-text search fused with RRF,
  optional reranker.
- **Grounded agent** — LangGraph pipeline (router → retrieve → answer →
  faithfulness judge → respond/escalate) with inline `[n]` citations and a
  hard refusal path; SSE streaming; Redis response cache.
- **Dashboard** — auth (JWT), onboarding wizard, knowledge-base manager,
  streaming playground with debug panel, conversations inbox with escalation
  queue.
- **Embeddable widget** — one script tag, iframe-isolated, streaming answers
  with citation popovers, thumbs feedback, CSAT rating, "talk to a human";
  loader ~1 KB gzipped.
- **Multi-tenancy hardening** — Postgres RLS (FORCE) with a non-superuser app
  role, rank-based RBAC (owner/admin/agent/viewer), member invites, real API
  keys (revocable widget + reveal-once secret), append-only audit log.
- **Analytics** — conversations over time, deflection & escalation rates,
  CSAT, top unanswered questions clustered by embedding similarity.
- **Observability & evals** — Langfuse traces per turn (per-node spans,
  costed generations, feedback scores), 125-item golden dataset, in-process
  eval runner (deterministic + RAGAS metrics), CI eval gate on agent/
  retrieval changes, nightly full run, online production sampling with
  regression alerts.
- **Deploy prep** — Docker image, Render blueprint, CD workflow, full
  runbook (`docs/deploy.md`), read-only public demo org with nightly reset.

## 🚧 In progress

- Live public deployment (Vercel + Render + Neon + Upstash) — everything is
  prepared in-repo; platform setup is the remaining manual step.
- README eval-results table sourced from committed eval reports.

## 🗺 Planned (post-MVP backlog)

- **WhatsApp channel** (Meta Cloud API), then email and Slack.
- **Agentic actions** via tool-calling: order lookup, refunds
  (Shopify/Stripe connectors).
- **White-labeling** + agency multi-workspace mode.
- **Self-hosted model serving** (vLLM/SGLang) for on-prem clients.
- **Voice channel** (STT → agent → TTS).
- **SSO/SAML**, data-retention controls, PII redaction pass.
- **Fine-tuned embeddings** (BGE) per large client corpus.
- Nightly analytics rollup table once query volume warrants it.
- Checkpointer cutover to the restricted DB role (tracked deviation from
  the RLS posture — see `docs/NOTES.md`).
