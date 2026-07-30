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

export interface QualityPoint {
  day: string;
  /** 0–1. */
  faithfulness: number;
  /** Answers scored that night. */
  sample_size: number;
}

/**
 * Latest online faithfulness for this org (plan 6.6).
 *
 * Every score is nullable: an org that has never been sampled has *no*
 * measurement, which is not the same as having scored zero. The card must say
 * "not measured yet" rather than render 0%, which reads as total failure.
 */
export interface QualitySummary {
  latest_faithfulness: number | null;
  latest_sample_size: number;
  latest_at: string | null;
  baseline_faithfulness: number | null;
  delta: number | null;
  alerted: boolean;
  trend: QualityPoint[];
}

export function fetchQuality(points = 14): Promise<QualitySummary> {
  return api<QualitySummary>(`/api/v1/analytics/quality?points=${points}`);
}

/** One decimal place: nightly faithfulness moves in fractions of a percent. */
export function formatScore(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}

/** Signed delta for the "vs previous" line, e.g. "+1.2 pts" / "−3.4 pts". */
export function formatDelta(delta: number): string {
  const points = delta * 100;
  const sign = points >= 0 ? "+" : "−";
  return `${sign}${Math.abs(points).toFixed(1)} pts`;
}
