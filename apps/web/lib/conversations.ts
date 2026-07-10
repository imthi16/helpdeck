import { api } from "@/lib/api";

export type ConversationStatus = "open" | "escalated" | "closed";
export type Channel = "playground" | "widget" | "api";

export interface ConversationSummary {
  id: string;
  channel: Channel;
  status: ConversationStatus;
  user_identifier: string | null;
  csat_score: number | null;
  message_count: number;
  created_at: string;
}

export interface TranscriptMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  citations: unknown[];
  confidence: number | null;
  created_at: string;
}

export interface EscalationInfo {
  id: string;
  reason: string;
  status: "pending" | "resolved";
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: TranscriptMessage[];
  escalations: EscalationInfo[];
}

export function listConversations(filters: {
  status?: ConversationStatus;
  channel?: Channel;
}): Promise<ConversationSummary[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.channel) params.set("channel", filters.channel);
  const query = params.toString();
  return api<ConversationSummary[]>(`/api/v1/conversations${query ? `?${query}` : ""}`);
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return api<ConversationDetail>(`/api/v1/conversations/${id}`);
}

export function resolveConversation(id: string): Promise<ConversationDetail> {
  return api<ConversationDetail>(`/api/v1/conversations/${id}/resolve`, { method: "POST" });
}

export function replyToConversation(id: string, content: string): Promise<ConversationDetail> {
  return api<ConversationDetail>(`/api/v1/conversations/${id}/reply`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}
