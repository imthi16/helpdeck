import { useEffect, useRef, useState } from "preact/hooks";

import { fetchConfig, sendFeedback, streamChat, type Citation, type WidgetConfig } from "./api";
import { MessageContent } from "./MessageContent";
import { closeWidget, readParams } from "./params";
import { loadSession, saveSession, type StoredMessage } from "./storage";

const params = readParams();

export function App() {
  const [config, setConfig] = useState<WidgetConfig | null>(null);
  const [messages, setMessages] = useState<StoredMessage[]>(() => loadSession(params.publicKey).messages);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const conversationId = useRef<string | undefined>(loadSession(params.publicKey).conversationId);
  const scroller = useRef<HTMLDivElement>(null);
  const color = config?.color ?? params.color;

  useEffect(() => {
    if (params.apiUrl) fetchConfig(params.apiUrl, params.publicKey).then(setConfig).catch(() => undefined);
  }, []);

  useEffect(() => {
    saveSession(params.publicKey, { conversationId: conversationId.current, messages });
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [messages]);

  const patchLast = (patch: (m: StoredMessage) => StoredMessage) =>
    setMessages((prev) => {
      const next = [...prev];
      next[next.length - 1] = patch(next[next.length - 1]);
      return next;
    });

  async function send(text: string) {
    if (!text.trim() || streaming || !params.apiUrl) return;
    setInput("");
    setStreaming(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, citations: [] },
      { role: "assistant", content: "", citations: [] },
    ]);

    await streamChat(
      params.apiUrl,
      params.publicKey,
      { message: text, conversation_id: conversationId.current },
      {
        onToken: (t) => patchLast((m) => ({ ...m, content: m.content + t })),
        onCitation: (c) => patchLast((m) => ({ ...m, citations: [...m.citations, c] })),
        onDone: (d) => {
          conversationId.current = d.conversation_id;
          patchLast((m) => ({ ...m, messageId: d.message_id, escalated: d.escalated }));
          setStreaming(false);
        },
        onError: (detail) => {
          patchLast((m) => ({ ...m, content: m.content || `Sorry, something went wrong (${detail}).` }));
          setStreaming(false);
        },
      },
    );
  }

  async function vote(index: number, rating: 1 | -1) {
    const message = messages[index];
    if (!message.messageId || !params.apiUrl) return;
    setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, feedback: rating } : m)));
    await sendFeedback(params.apiUrl, params.publicKey, message.messageId, rating);
  }

  return (
    <div class="app">
      <header class="header" style={{ background: color }}>
        <span class="title" data-testid="widget-title">
          {config?.org_name ?? "HelpDeck"}
        </span>
        <button class="close" aria-label="Close chat" onClick={closeWidget}>
          &#10005;
        </button>
      </header>

      <div class="body" ref={scroller} data-testid="widget-body">
        {messages.length === 0 && (
          <p class="welcome" data-testid="widget-welcome">
            {config?.welcome_message ?? "Hi! How can I help you today?"}
          </p>
        )}
        {messages.map((message, index) => (
          <div class={`msg ${message.role}`} data-testid={`msg-${message.role}`}>
            {message.role === "assistant" ? (
              <>
                <MessageContent
                  text={message.content}
                  citations={message.citations}
                  onCitation={setActiveCitation}
                />
                {message.escalated && (
                  <p class="handoff" data-testid="handoff">
                    We&apos;ve notified our team — a human will follow up.
                  </p>
                )}
                {message.messageId && !streaming && (
                  <div class="votes">
                    <button
                      aria-label="Helpful"
                      data-testid="thumbs-up"
                      class={message.feedback === 1 ? "voted" : ""}
                      onClick={() => vote(index, 1)}
                    >
                      &#128077;
                    </button>
                    <button
                      aria-label="Not helpful"
                      data-testid="thumbs-down"
                      class={message.feedback === -1 ? "voted" : ""}
                      onClick={() => vote(index, -1)}
                    >
                      &#128078;
                    </button>
                  </div>
                )}
              </>
            ) : (
              message.content
            )}
          </div>
        ))}
      </div>

      {activeCitation && (
        <div class="popover" data-testid="source-popover">
          <div class="popover-head">
            <strong>{activeCitation.document_title}</strong>
            <button aria-label="Close source" onClick={() => setActiveCitation(null)}>
              &#10005;
            </button>
          </div>
          <p>{activeCitation.snippet}</p>
        </div>
      )}

      <div class="footer">
        <button
          class="human"
          data-testid="talk-to-human"
          disabled={streaming}
          onClick={() => send("I would like to talk to a human agent.")}
        >
          Talk to a human
        </button>
        <form
          class="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <input
            value={input}
            data-testid="widget-input"
            placeholder="Type a message…"
            disabled={streaming}
            onInput={(e) => setInput((e.target as HTMLInputElement).value)}
          />
          <button type="submit" style={{ background: color }} disabled={streaming || !input.trim()}>
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
