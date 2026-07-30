"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatDelta,
  formatScore,
  type QualityPoint,
  type QualitySummary,
} from "@/lib/analytics";

/**
 * Quality card (plan 6.6): the latest nightly faithfulness score plus a trend.
 *
 * Three things this deliberately refuses to do:
 *
 * - Render a missing measurement as 0%. An org that has never been sampled has no
 *   score; 0% would read as "every answer is a hallucination".
 * - Show a score without its sample size. A 100% from two answers and a 100% from
 *   eighty are very different claims.
 * - Draw a trend line through one point. A single datapoint has no direction, and
 *   a flat line implies a stability nobody has measured.
 */

/** Bars rather than a line: nightly samples are discrete measurements, not a
 *  continuous signal, and a line would imply values between them that nobody
 *  measured. Height is scaled from 0.5 so ordinary movement is legible — the
 *  interesting range for faithfulness is the top half, not 0–100%. */
function Sparkline({ points }: { points: QualityPoint[] }) {
  const floor = 0.5;
  return (
    <div className="flex h-10 items-end gap-0.5" aria-hidden="true">
      {points.map((point) => {
        const height = Math.max(
          4,
          Math.min(100, ((point.faithfulness - floor) / (1 - floor)) * 100),
        );
        return (
          <div
            key={point.day}
            className="flex-1 rounded-sm bg-primary/25"
            style={{ height: `${height}%` }}
            title={`${point.day}: ${formatScore(point.faithfulness)} (${point.sample_size} answers)`}
          />
        );
      })}
    </div>
  );
}

export function QualityCard({
  quality,
  loading,
}: {
  quality: QualitySummary | null;
  loading: boolean;
}) {
  const measured = quality?.latest_faithfulness != null;

  return (
    <Card size="sm" data-testid="quality-card">
      <CardHeader>
        <CardTitle>Answer quality</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !measured ? (
          // Explains *why* there is no number, so an empty card does not read as
          // a broken one. Sampling only scores answers that cited sources, so a
          // new org and an all-refusals org both land here legitimately.
          <div className="space-y-1">
            <p className="text-2xl font-semibold tabular-nums text-muted-foreground">
              Not measured yet
            </p>
            <p className="text-xs text-muted-foreground">
              Faithfulness is sampled nightly from answers that cite your sources.
            </p>
          </div>
        ) : (
          <>
            <div className="flex items-baseline gap-2">
              <span
                className="text-2xl font-semibold tabular-nums"
                data-testid="quality-score"
              >
                {formatScore(quality!.latest_faithfulness!)}
              </span>
              {quality!.delta != null && (
                <span
                  className={
                    quality!.delta < 0
                      ? "text-xs font-medium text-destructive"
                      : "text-xs font-medium text-muted-foreground"
                  }
                  data-testid="quality-delta"
                >
                  {formatDelta(quality!.delta)} vs previous
                </span>
              )}
            </div>

            <p className="text-xs text-muted-foreground">
              {quality!.latest_sample_size}{" "}
              {quality!.latest_sample_size === 1 ? "answer" : "answers"} scored
              {quality!.latest_at
                ? ` on ${new Date(quality!.latest_at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}`
                : ""}
            </p>

            {quality!.alerted && (
              <p
                className="text-xs font-medium text-destructive"
                data-testid="quality-alert"
              >
                Faithfulness dropped sharply against the last 7 days.
              </p>
            )}

            {quality!.trend.length > 1 && <Sparkline points={quality!.trend} />}
          </>
        )}
      </CardContent>
    </Card>
  );
}
