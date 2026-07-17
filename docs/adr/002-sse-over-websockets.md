# ADR-002: SSE over WebSockets for chat streaming

**Status:** accepted (Phase 2, 2026)

## Context

The chat endpoint streams tokens, citations, and status events to the
playground and the embeddable widget. Candidates: WebSockets, Server-Sent
Events (SSE), or long-polling.

## Decision

**SSE** (`sse-starlette`) for all chat streaming: `status`, `token`,
`citation`, `done`, `error` events with a ~15 s heartbeat.

## Rationale

- **The traffic is one-directional.** The client sends one request and
  receives a stream; there is no client→server traffic mid-turn that would
  justify a duplex socket.
- **It's plain HTTP.** SSE traverses proxies, CDNs, and corporate
  middleboxes that routinely break WebSocket upgrades — which matters for a
  widget embedded on arbitrary customer sites.
- **Simpler auth and ops.** The same cookie/header auth, CORS, and rate
  limiting apply unchanged; no connection registry or socket lifecycle.
- **Server simplicity.** Each turn is a normal request handler with an async
  generator — no socket lifecycle to manage across LangGraph runs.

## Consequences

- True bidirectional features (e.g. live agent co-typing) would need either
  a second SSE channel or a WebSocket added for that feature alone.
- Buffering middle-boxes must be told not to buffer (`X-Accel-Buffering:
  no`), and heartbeats keep idle connections alive.
- **No automatic recovery today.** Both clients stream a `POST` response via
  `fetch`, not a native `EventSource`, so a dropped connection simply ends
  the turn — nothing reconnects or resumes, and naively retrying the POST
  would run (and bill) the turn twice. An idempotent resume protocol is
  future work if interrupted turns become a real problem.
