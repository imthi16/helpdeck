"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api";
import { fetchAnalyticsOverview, type AnalyticsOverview } from "@/lib/analytics";

// Series color validated for both themes (dataviz palette slot 1).
const SERIES_LIGHT = "#2a78d6";
const SERIES_DARK = "#3987e5";

const WINDOWS = [7, 30, 90] as const;

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function subscribeToColorScheme(callback: () => void) {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", callback);
  return () => media.removeEventListener("change", callback);
}

function useIsDark(): boolean {
  return useSyncExternalStore(
    subscribeToColorScheme,
    () => window.matchMedia("(prefers-color-scheme: dark)").matches,
    () => false,
  );
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card data-testid={`stat-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-3xl tabular-nums">{value}</CardTitle>
      </CardHeader>
      {hint ? (
        <CardContent className="pt-0 text-xs text-muted-foreground">{hint}</CardContent>
      ) : null}
    </Card>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isDark = useIsDark();

  useEffect(() => {
    let active = true;
    fetchAnalyticsOverview(days)
      .then((overview) => {
        if (active) setData(overview);
      })
      .catch((err) => {
        if (active) setError(err instanceof ApiError ? err.message : "Failed to load analytics");
      });
    return () => {
      active = false;
    };
  }, [days]);

  const series = isDark ? SERIES_DARK : SERIES_LIGHT;
  const perDay = (data?.conversations_per_day ?? []).map((d) => ({
    ...d,
    label: d.date.slice(5), // MM-DD
  }));

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Analytics"
        description="Conversation volume, deflection, and unanswered questions."
        actions={
          <div className="flex gap-1">
            {WINDOWS.map((window) => (
              <Button
                key={window}
                size="sm"
                variant={window === days ? "default" : "outline"}
                onClick={() => setDays(window)}
                data-testid={`window-${window}`}
              >
                {window}d
              </Button>
            ))}
          </div>
        }
      />

      {error ? (
        <p className="text-sm text-destructive" data-testid="analytics-error">
          {error}
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile
          label="Conversations"
          value={data ? String(data.total_conversations) : "—"}
          hint={`last ${days} days`}
        />
        <StatTile
          label="Deflection rate"
          value={formatPercent(data?.deflection_rate ?? null)}
          hint="answered without escalation"
        />
        <StatTile
          label="Escalation rate"
          value={formatPercent(data?.escalation_rate ?? null)}
          hint={`${data?.escalated_conversations ?? 0} escalated`}
        />
        <StatTile
          label="CSAT"
          value={data?.csat_average != null ? data.csat_average.toFixed(1) : "—"}
          hint={`${data?.csat_responses ?? 0} responses (1–5)`}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Conversations over time</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-64 w-full" data-testid="conversations-chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={perDay} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                <CartesianGrid strokeOpacity={0.15} vertical={false} />
                <XAxis
                  dataKey="label"
                  tickLine={false}
                  axisLine={false}
                  fontSize={11}
                  minTickGap={24}
                  stroke="currentColor"
                  opacity={0.55}
                />
                <YAxis
                  allowDecimals={false}
                  tickLine={false}
                  axisLine={false}
                  fontSize={11}
                  stroke="currentColor"
                  opacity={0.55}
                />
                <Tooltip
                  cursor={{ stroke: series, strokeOpacity: 0.4 }}
                  contentStyle={{
                    background: "var(--popover)",
                    color: "var(--popover-foreground)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelFormatter={(label) => `Day ${label}`}
                  formatter={(value) => [String(value), "conversations"]}
                />
                <Area
                  type="monotone"
                  dataKey="count"
                  stroke={series}
                  strokeWidth={2}
                  fill={series}
                  fillOpacity={0.12}
                  activeDot={{ r: 4 }}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Top unanswered questions</CardTitle>
          <CardDescription>
            Questions that led to a low-confidence answer or an escalation, grouped by similarity.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table data-testid="unanswered-table">
            <TableHeader>
              <TableRow>
                <TableHead>Question</TableHead>
                <TableHead className="w-24 text-right">Count</TableHead>
                <TableHead className="w-40">Last seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data?.top_unanswered ?? []).map((cluster) => (
                <TableRow key={cluster.question}>
                  <TableCell>{cluster.question}</TableCell>
                  <TableCell className="text-right tabular-nums">{cluster.count}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {new Date(cluster.last_seen).toLocaleDateString()}
                  </TableCell>
                </TableRow>
              ))}
              {data && data.top_unanswered.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="text-muted-foreground">
                    Nothing unanswered in this window. 🎉
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
