"use client";

import { useRef, useState } from "react";

import { MessageContent } from "@/components/message-content";
import { PageHeader } from "@/components/page-header";
import { ThresholdGauge } from "@/components/threshold-gauge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { streamChat, type Citation, type DebugInfo } from "@/lib/chat";
import { useSession } from "@/lib/session";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
}

export default function PlaygroundPage() {
  const { user } = useSession();
  const orgId = user?.memberships[0]?.org_id;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [debug, setDebug] = useState<DebugInfo | null>(null);
  const [copiedTrace, setCopiedTrace] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const conversationId = useRef<string | undefined>(undefined);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!orgId || !input.trim() || streaming) return;

    const question = input.trim();
    setInput("");
    setDebug(null);
    setStreaming(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question, citations: [] },
      { role: "assistant", content: "", citations: [] },
    ]);

    const appendToAssistant = (patch: (m: ChatMessage) => ChatMessage) =>
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = patch(next[next.length - 1]);
        return next;
      });

    await streamChat(
      {
        org_id: orgId,
        message: question,
        conversation_id: conversationId.current,
        debug: true,
        bypass_cache: true,
      },
      {
        onStatus: (stage) => setStatus(stage),
        onToken: (text) =>
          appendToAssistant((m) => ({ ...m, content: m.content + text })),
        onCitation: (citation) =>
          appendToAssistant((m) => ({ ...m, citations: [...m.citations, citation] })),
        onDebug: (info) => setDebug(info),
        onDone: (done) => {
          conversationId.current = done.conversation_id;
          setStatus(null);
          setStreaming(false);
        },
        onError: (detail) => {
          appendToAssistant((m) => ({ ...m, content: m.content || `Error: ${detail}` }));
          setStatus(null);
          setStreaming(false);
        },
      },
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Playground"
        // True of this surface specifically: the playground always re-runs the
        // agent instead of serving the cached answer, and asks for the debug feed.
        eyebrow="Cache bypassed · debug on"
        description="Ask your assistant a question and inspect how it answers."
      />
      <div className="grid items-start gap-4 lg:grid-cols-[1fr_21rem]">
        <Card className="flex min-h-[28rem] flex-col">
          <CardContent className="flex flex-1 flex-col gap-4 p-4">
            <div className="flex-1 space-y-5 overflow-y-auto" data-testid="chat-log">
              {messages.length === 0 && (
                <p className="max-w-sm text-sm text-muted-foreground">
                  Ask something your knowledge base can answer. Every reply cites the passage it
                  came from — the panel beside it shows what was retrieved.
                </p>
              )}
              {messages.map((message, index) => (
                <div
                  key={index}
                  data-testid={message.role === "assistant" ? "assistant-message" : "user-message"}
                  className={
                    message.role === "user"
                      ? "ml-auto max-w-[80%] rounded-lg rounded-br-sm bg-primary px-3.5 py-2.5 text-sm text-primary-foreground"
                      // The assistant speaks in the page's own voice: plain text on
                      // paper, marked in the margin, so citations are the only
                      // colour in the reply.
                      : "mr-auto max-w-[92%] border-l-2 border-mark pl-4 text-[0.95rem] leading-relaxed"
                  }
                >
                  {message.role === "assistant" ? (
                    <MessageContent text={message.content} citations={message.citations} />
                  ) : (
                    message.content
                  )}
                </div>
              ))}
              {status && (
                <p className="eyebrow flex items-center gap-2" data-testid="chat-status">
                  <span
                    aria-hidden
                    className="size-1.5 animate-pulse rounded-full bg-mark motion-reduce:animate-none"
                  />
                  {status}…
                </p>
              )}
            </div>
            <form onSubmit={send} className="flex gap-2">
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask a question…"
                data-testid="chat-input"
                disabled={streaming}
              />
              <Button type="submit" disabled={streaming || !input.trim()}>
                Send
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card data-testid="debug-panel" className="h-fit">
          <CardHeader className="border-b">
            <CardTitle className="text-sm">Evidence</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-xs">
            {!debug ? (
              <p className="text-muted-foreground">
                Send a message to see what the answer was built from.
              </p>
            ) : (
              <>
                {/* Confidence against the line it was judged on: above it the
                    assistant answers, below it the conversation goes to a human. */}
                <div>
                  <div className="flex items-baseline justify-between">
                    <span className="eyebrow">Faithfulness</span>
                    <span className="data text-sm" data-testid="debug-confidence">
                      {debug.confidence != null ? debug.confidence.toFixed(2) : "—"}
                    </span>
                  </div>
                  <ThresholdGauge
                    className="mt-2"
                    size="sm"
                    value={debug.confidence ?? 0}
                    threshold={debug.threshold ?? undefined}
                    label={`faithfulness ${debug.confidence ?? 0} against threshold ${debug.threshold ?? "unset"}`}
                  />
                  {debug.threshold != null && (
                    <p className="eyebrow mt-2">
                      Handoff below {debug.threshold.toFixed(2)}
                    </p>
                  )}
                </div>

                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 border-t pt-3">
                  <dt className="eyebrow self-center">Intent</dt>
                  <dd className="text-right text-[0.8rem]">{debug.intent ?? "—"}</dd>
                  <dt className="eyebrow self-center">Model</dt>
                  <dd className="data truncate text-right text-[0.7rem]">{debug.model ?? "—"}</dd>
                  <dt className="eyebrow self-center">Latency</dt>
                  <dd className="data text-right text-[0.75rem]">{debug.latency_ms} ms</dd>
                  <dt className="eyebrow self-center">Tokens</dt>
                  <dd className="data text-right text-[0.75rem]">
                    {debug.tokens_in ?? 0} in / {debug.tokens_out ?? 0} out
                  </dd>
                  {/* The turn's Langfuse trace. Copyable rather than a link: the
                      browser has no LANGFUSE_HOST, and guessing one is worse than an
                      id you can paste into whichever project you configured. Absent
                      when tracing is off, or when the answer came from cache — a
                      cached turn runs no agent and so has no trace of its own. */}
                  <dt className="eyebrow self-center">Trace</dt>
                  <dd className="text-right">
                    {debug.trace_id ? (
                      <button
                        type="button"
                        data-testid="debug-trace-id"
                        title={`Copy trace id ${debug.trace_id}`}
                        onClick={() => {
                          void navigator.clipboard?.writeText(debug.trace_id ?? "");
                          setCopiedTrace(true);
                          window.setTimeout(() => setCopiedTrace(false), 1500);
                        }}
                        className="data max-w-full cursor-pointer truncate text-[0.7rem] underline decoration-dotted underline-offset-2 hover:opacity-70"
                      >
                        {copiedTrace ? "Copied" : debug.trace_id}
                      </button>
                    ) : (
                      <span
                        data-testid="debug-trace-id"
                        className="data text-[0.7rem]"
                        title="No trace: tracing is off, or this answer was served from cache"
                      >
                        —
                      </span>
                    )}
                  </dd>
                </dl>

                <div className="border-t pt-3">
                  <p className="eyebrow mb-2">Retrieved · {debug.chunks.length} passages</p>
                  {/* Scrolls on its own so the panel never dictates the height of
                      the conversation beside it. */}
                  <ul
                    className="max-h-96 space-y-px overflow-y-auto pr-1"
                    data-testid="debug-chunks"
                  >
                    {debug.chunks.map((chunk) => (
                      <li key={chunk.n} className="group py-2">
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="flex min-w-0 items-baseline gap-1.5">
                            <span className="data shrink-0 text-[0.7rem] text-muted-foreground">
                              [{chunk.n}]
                            </span>
                            <span className="truncate font-medium">{chunk.document_title}</span>
                          </span>
                          <span className="data shrink-0 text-[0.7rem] text-muted-foreground">
                            {chunk.score.toFixed(4)}
                          </span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-muted-foreground">{chunk.snippet}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
