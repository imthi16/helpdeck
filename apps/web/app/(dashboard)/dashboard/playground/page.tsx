"use client";

import { useRef, useState } from "react";

import { MessageContent } from "@/components/message-content";
import { PageHeader } from "@/components/page-header";
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
        description="Ask your assistant a question and inspect how it answers."
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card className="flex min-h-[28rem] flex-col">
          <CardContent className="flex flex-1 flex-col gap-4 p-4">
            <div className="flex-1 space-y-4 overflow-y-auto" data-testid="chat-log">
              {messages.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  Ask something your knowledge base can answer.
                </p>
              )}
              {messages.map((message, index) => (
                <div
                  key={index}
                  data-testid={message.role === "assistant" ? "assistant-message" : "user-message"}
                  className={
                    message.role === "user"
                      ? "ml-auto max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                      : "mr-auto max-w-[90%] rounded-lg bg-muted px-3 py-2"
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
                <p className="text-xs text-muted-foreground" data-testid="chat-status">
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

        <Card data-testid="debug-panel">
          <CardHeader>
            <CardTitle className="text-sm">Debug</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            {!debug ? (
              <p className="text-muted-foreground">Send a message to see retrieval details.</p>
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-y-1">
                  <dt className="text-muted-foreground">Intent</dt>
                  <dd>{debug.intent ?? "—"}</dd>
                  <dt className="text-muted-foreground">Model</dt>
                  <dd className="truncate">{debug.model ?? "—"}</dd>
                  <dt className="text-muted-foreground">Faithfulness</dt>
                  <dd data-testid="debug-confidence" title="Judge score: every claim supported?">
                    {debug.confidence != null ? debug.confidence.toFixed(2) : "—"}
                  </dd>
                  <dt className="text-muted-foreground">Trace</dt>
                  <dd
                    className="truncate font-mono text-xs"
                    data-testid="debug-trace"
                    title="Langfuse trace id"
                  >
                    {debug.trace_id ?? "—"}
                  </dd>
                  <dt className="text-muted-foreground">Latency</dt>
                  <dd>{debug.latency_ms} ms</dd>
                  <dt className="text-muted-foreground">Tokens</dt>
                  <dd>
                    {debug.tokens_in ?? 0} in / {debug.tokens_out ?? 0} out
                  </dd>
                </dl>
                <div>
                  <p className="mb-1 font-medium">Retrieved chunks</p>
                  <ul className="space-y-2" data-testid="debug-chunks">
                    {debug.chunks.map((chunk) => (
                      <li key={chunk.n} className="rounded border p-2">
                        <div className="flex justify-between">
                          <span className="font-medium">
                            [{chunk.n}] {chunk.document_title}
                          </span>
                          <span className="text-muted-foreground">
                            {chunk.score.toFixed(4)}
                          </span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-muted-foreground">
                          {chunk.snippet}
                        </p>
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
