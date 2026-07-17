import type { Citation } from "./api";

export interface StoredMessage {
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  messageId?: string;
  escalated?: boolean;
  feedback?: 1 | -1;
}

export interface StoredSession {
  conversationId?: string;
  messages: StoredMessage[];
  // Set once the user rated (or dismissed) the CSAT prompt for this session.
  csatDone?: boolean;
}

const key = (publicKey: string) => `helpdeck:session:${publicKey}`;

export function loadSession(publicKey: string): StoredSession {
  try {
    const raw = localStorage.getItem(key(publicKey));
    if (raw) return JSON.parse(raw) as StoredSession;
  } catch {
    // ignore malformed / unavailable storage
  }
  return { messages: [] };
}

export function saveSession(publicKey: string, session: StoredSession): void {
  try {
    localStorage.setItem(key(publicKey), JSON.stringify(session));
  } catch {
    // storage may be unavailable (private mode); degrade gracefully
  }
}
