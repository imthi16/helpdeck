export interface Citation {
  n: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  snippet: string;
}

export interface DoneInfo {
  message_id: string;
  conversation_id: string;
  escalated: boolean;
}

export interface ChatHandlers {
  onToken?: (text: string) => void;
  onCitation?: (citation: Citation) => void;
  onDone?: (done: DoneInfo) => void;
  onError?: (detail: string) => void;
}

export interface WidgetConfig {
  org_name: string;
  welcome_message: string;
  color: string;
}

export async function fetchConfig(apiUrl: string, publicKey: string): Promise<WidgetConfig | null> {
  try {
    const response = await fetch(`${apiUrl}/api/v1/widget/config`, {
      headers: { "X-Public-Key": publicKey },
    });
    return response.ok ? ((await response.json()) as WidgetConfig) : null;
  } catch {
    return null;
  }
}

export async function sendFeedback(
  apiUrl: string,
  publicKey: string,
  messageId: string,
  rating: 1 | -1,
): Promise<void> {
  await fetch(`${apiUrl}/api/v1/widget/feedback`, {
    method: "POST",
    headers: { "X-Public-Key": publicKey, "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId, rating }),
  });
}

export async function streamChat(
  apiUrl: string,
  publicKey: string,
  body: { message: string; conversation_id?: string; user_identifier?: string },
  handlers: ChatHandlers,
): Promise<void> {
  const response = await fetch(`${apiUrl}/api/v1/widget/chat`, {
    method: "POST",
    headers: { "X-Public-Key": publicKey, "Content-Type": "application/json" },
    body: JSON.stringify(body),
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
    if (event === "token") handlers.onToken?.((payload as { text: string }).text);
    else if (event === "citation") handlers.onCitation?.(payload as Citation);
    else if (event === "done") handlers.onDone?.(payload as DoneInfo);
    else if (event === "error") handlers.onError?.((payload as { detail: string }).detail);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
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
