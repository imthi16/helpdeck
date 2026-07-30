"use client";

import { useEffect, useState } from "react";

import { ConversationsChart } from "@/components/conversations-chart";
import { PageHeader } from "@/components/page-header";
import { QualityCard } from "@/components/quality-card";
import { ThresholdGauge } from "@/components/threshold-gauge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  RANGES,
  fetchOverview,
  fetchQuality,
  formatCount,
  formatDay,
  formatPercent,
  type AnalyticsOverview,
  type QualitySummary,
  type RangeDays,
} from "@/lib/analytics";

/**
 * Label · value (+ optional context line). No sparkline: the chart below is the
 * trend. `share` draws the value on the console's track when it is a fraction of
 * the whole — deflection and escalation are two halves of one number, and seeing
 * them as lengths says that faster than two percentages side by side.
 */
function StatTile({
  label,
  value,
  context,
  testId,
  share,
  tone,
}: {
  label: string;
  value: string;
  context?: string;
  testId: string;
  share?: number | null;
  tone?: "grounded" | "handoff";
}) {
  return (
    <Card size="sm">
      <CardContent>
        <p className="eyebrow">{label}</p>
        <p className="mt-2 font-display text-[1.75rem] font-semibold leading-none" data-testid={testId}>
          {value}
        </p>
        {share != null && tone && (
          <ThresholdGauge
            className="mt-3"
            size="sm"
            value={share}
            tone={tone}
            label={`${label} ${value}`}
          />
        )}
        {context && <p className="mt-2 text-xs text-muted-foreground">{context}</p>}
      </CardContent>
    </Card>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = useState<RangeDays>(30);
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showTable, setShowTable] = useState(false);
  const [quality, setQuality] = useState<QualitySummary | null>(null);
  const [qualityLoading, setQualityLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetchOverview(days)
      .then((overview) => {
        if (active) setData(overview);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load analytics");
      });
    return () => {
      active = false;
    };
  }, [days]);

  // Separate from the overview fetch, and deliberately not gated on it: quality
  // comes from nightly eval_runs rather than from the conversation tables, so the
  // range selector does not apply and a failure here must not blank the page.
  useEffect(() => {
    let active = true;
    fetchQuality()
      .then((summary) => {
        if (active) setQuality(summary);
      })
      .catch(() => {
        if (active) setQuality(null);
      })
      .finally(() => {
        if (active) setQualityLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Analytics"
        description="Deflection, escalation, and satisfaction across your conversations."
      />

      {/* Filters in one row above the charts. */}
      <div className="mb-4 flex gap-2">
        {RANGES.map((range) => (
          <Button
            key={range}
            size="sm"
            variant={range === days ? "default" : "outline"}
            onClick={() => setDays(range)}
            data-testid={`range-${range}`}
          >
            {range}d
          </Button>
        ))}
      </div>

      {error && (
        <p role="alert" className="mb-4 text-sm text-red-600">
          {error}
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Conversations"
          value={data ? formatCount(data.total_conversations) : "—"}
          context={`Last ${days} days`}
          testId="stat-conversations"
        />
        <StatTile
          label="Deflection rate"
          value={data ? formatPercent(data.deflection_rate) : "—"}
          context={data ? `${data.deflected_conversations} answered without a handoff` : undefined}
          testId="stat-deflection"
          share={data?.deflection_rate}
          tone="grounded"
        />
        <StatTile
          label="Escalation rate"
          value={data ? formatPercent(data.escalation_rate) : "—"}
          context={data ? `${data.escalated_conversations} handed to a human` : undefined}
          testId="stat-escalation"
          share={data?.escalation_rate}
          tone="handoff"
        />
        <StatTile
          label="CSAT"
          value={data?.csat_average != null ? `${data.csat_average.toFixed(1)} / 5` : "—"}
          context={
            data
              ? data.csat_responses === 0
                ? "No ratings yet"
                : `${data.csat_responses} rating${data.csat_responses === 1 ? "" : "s"}`
              : undefined
          }
          testId="stat-csat"
        />
      </div>

      <Card className="mt-6">
        <CardHeader>
          <div className="flex items-center justify-between gap-4">
            <CardTitle className="text-base">Conversations per day</CardTitle>
            <Button variant="outline" size="sm" onClick={() => setShowTable((prev) => !prev)}>
              {showTable ? "Show chart" : "Show data"}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {!data ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : showTable ? (
            // The table view: same numbers, no reliance on reading a chart.
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Day</TableHead>
                  <TableHead className="text-right">Conversations</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.conversations_over_time.map((point) => (
                  <TableRow key={point.day}>
                    <TableCell>{formatDay(point.day)}</TableCell>
                    <TableCell className="text-right tabular-nums">{point.conversations}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <ConversationsChart data={data.conversations_over_time} />
          )}
        </CardContent>
      </Card>

      <div className="mt-6">
        <QualityCard quality={quality} loading={qualityLoading} />
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-base">Top unanswered questions</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Question</TableHead>
                <TableHead className="text-right">Times asked</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!data || data.top_unanswered.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={2} className="text-center text-muted-foreground">
                    Nothing escalated in this period.
                  </TableCell>
                </TableRow>
              ) : (
                data.top_unanswered.map((cluster) => (
                  <TableRow key={cluster.question} data-testid="unanswered-row">
                    <TableCell>
                      {cluster.question}
                      {cluster.variants.length > 1 && (
                        <span className="block text-xs text-muted-foreground">
                          + {cluster.variants.length - 1} similar wording
                          {cluster.variants.length > 2 ? "s" : ""}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{cluster.count}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
          <p className="mt-3 text-sm text-muted-foreground">
            Questions that escalated or came back poorly grounded, grouped by similarity — the gaps
            worth filling in your knowledge base.
          </p>
        </CardContent>
      </Card>

      {data && (
        <p className="mt-4 text-sm text-muted-foreground" data-testid="thumbs-summary">
          Answer feedback: {data.thumbs_up} 👍 · {data.thumbs_down} 👎
        </p>
      )}
    </div>
  );
}
