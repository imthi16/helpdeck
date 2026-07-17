import { api } from "@/lib/api";

export interface UnansweredCluster {
  question: string;
  count: number;
  last_seen: string;
}

export interface AnalyticsOverview {
  days: number;
  total_conversations: number;
  escalated_conversations: number;
  answered_conversations: number;
  escalation_rate: number | null;
  deflection_rate: number | null;
  csat_average: number | null;
  csat_responses: number;
  conversations_per_day: { date: string; count: number }[];
  top_unanswered: UnansweredCluster[];
}

export function fetchAnalyticsOverview(days = 30): Promise<AnalyticsOverview> {
  return api<AnalyticsOverview>(`/api/v1/analytics/overview?days=${days}`);
}
