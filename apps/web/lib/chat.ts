import { API_URL } from "@/lib/api";

export interface Citation {
  n: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  snippet: string;
}

export interface DebugChunk {
  n: number;
  document_title: string;
  score: number;
  snippet: string;
}

export interface DebugInfo {
  intent: string | null;
  model: string | null;
  confidence: number | null;
  latency_ms: number;
  tokens_in: number | null;
  tokens_out: number | null;
  chunks: DebugChunk[];
}

export interface DoneInfo {
  message_id: string;
  conversation_id: string;
  confidence: number | null;
  escalated: boolean;
  cached: boolean;
}

export interface ChatHandlers {
  onStatus?: (stage: string) => void;
  onToken?: (text: string) => void;
  onCitation?: (citation: Citation) => void;
  onDebug?: (debug: DebugInfo) => void;
  onDone?: (done: DoneInfo) => void;
  onError?: (detail: string) => void;
}

export interface ChatRequestBody {
  org_id: string;
  message: string;
  conversation_id?: string;
  debug?: boolean;
  bypass_cache?: boolean;
}

/** POST to the SSE chat endpoint and dispatch parsed events to handlers. */
export async function streamChat(
  body: ChatRequestBody,
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/api/v1/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    handlers.onError?.(`chat failed (${response.status})`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (event: string, data: string) => {
    let payload: unknown;
    try {
      payload = JSON.parse(data);
    } catch {
      return;
    }
    switch (event) {
      case "status":
        handlers.onStatus?.((payload as { stage: string }).stage);
        break;
      case "token":
        handlers.onToken?.((payload as { text: string }).text);
        break;
      case "citation":
        handlers.onCitation?.(payload as Citation);
        break;
      case "debug":
        handlers.onDebug?.(payload as DebugInfo);
        break;
      case "done":
        handlers.onDone?.(payload as DoneInfo);
        break;
      case "error":
        handlers.onError?.((payload as { detail: string }).detail);
        break;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // Normalize CRLF; sse-starlette delimits events with \r\n\r\n.
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) dispatch(event, data);
    }
  }
}
