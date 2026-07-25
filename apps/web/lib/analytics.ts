import { api } from "@/lib/api";

export interface DayCount {
  day: string;
  conversations: number;
}

export interface QuestionCluster {
  question: string;
  count: number;
  variants: string[];
}

export interface AnalyticsOverview {
  days: number;
  total_conversations: number;
  escalated_conversations: number;
  deflected_conversations: number;
  /** 0–1 fractions. */
  escalation_rate: number;
  deflection_rate: number;
  csat_average: number | null;
  csat_responses: number;
  thumbs_up: number;
  thumbs_down: number;
  conversations_over_time: DayCount[];
  top_unanswered: QuestionCluster[];
}

export const RANGES = [7, 30, 90] as const;
export type RangeDays = (typeof RANGES)[number];

export function fetchOverview(days: RangeDays = 30): Promise<AnalyticsOverview> {
  return api<AnalyticsOverview>(`/api/v1/analytics/overview?days=${days}`);
}

export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/** Compact form for stat-tile values: 1,284 / 12.9K. */
export function formatCount(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

export function formatDay(day: string): string {
  return new Date(`${day}T00:00:00`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}
