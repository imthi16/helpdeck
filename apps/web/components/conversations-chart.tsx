"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDay, type DayCount } from "@/lib/analytics";

/**
 * Conversations per day: magnitude over time, one series.
 *
 * Single series, so no legend — the card title says what is plotted. Colours are
 * the validated blue for both modes (light #2a78d6 / dark #3987e5), declared as
 * scoped custom properties so the mode swap happens in one place; grid and axes
 * are recessive hairlines; the busiest day is the only directly labelled bar,
 * with the rest carried by the axis and the hover tooltip.
 */
export function ConversationsChart({ data }: { data: DayCount[] }) {
  const peak = data.reduce((max, point) => Math.max(max, point.conversations), 0);
  const peakIndex = data.findIndex((point) => point.conversations === peak && peak > 0);
  // Only the busiest day carries a value label; a number on every bar goes unread.
  const points = data.map((point, index) => ({
    ...point,
    peakLabel: index === peakIndex ? String(point.conversations) : "",
  }));

  return (
    <div className="viz-root w-full">
      <style>{`
        .viz-root {
          --series-1: #2a78d6;
          --grid: #e1e0d9;
          --axis-ink: #898781;
          --surface-1: #ffffff;
          --tooltip-ink: #0b0b0b;
        }
        @media (prefers-color-scheme: dark) {
          :root:where(:not([data-theme="light"])) .viz-root {
            --series-1: #3987e5;
            --grid: #2c2c2a;
            --axis-ink: #898781;
            --surface-1: #0a0a0a;
            --tooltip-ink: #ffffff;
          }
        }
        :root[data-theme="dark"] .viz-root {
          --series-1: #3987e5;
          --grid: #2c2c2a;
          --axis-ink: #898781;
          --surface-1: #0a0a0a;
          --tooltip-ink: #ffffff;
        }
      `}</style>
      <div className="h-64 w-full" data-testid="conversations-chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={points}
            margin={{ top: 20, right: 12, bottom: 4, left: 4 }}
            barCategoryGap="18%"
          >
            {/* Hairline, solid, horizontal only — recessive by design. */}
            <CartesianGrid stroke="var(--grid)" strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="day"
              tickFormatter={formatDay}
              tick={{ fill: "var(--axis-ink)", fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: "var(--grid)" }}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis
              allowDecimals={false}
              width={32}
              tick={{ fill: "var(--axis-ink)", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--grid)", fillOpacity: 0.35 }}
              labelFormatter={(day) => formatDay(String(day))}
              formatter={(value) => [String(value ?? 0), "Conversations"]}
              contentStyle={{
                background: "var(--surface-1)",
                border: "1px solid var(--grid)",
                borderRadius: 6,
                fontSize: 12,
              }}
              // Tooltip text wears ink, not the series colour.
              itemStyle={{ color: "var(--tooltip-ink)" }}
              labelStyle={{ color: "var(--tooltip-ink)" }}
            />
            <Bar
              dataKey="conversations"
              fill="var(--series-1)"
              // 4px rounded data-end, square at the baseline; capped thickness so
              // the band keeps some air.
              radius={[4, 4, 0, 0]}
              maxBarSize={24}
              isAnimationActive={false}
            >
              <LabelList
                dataKey="peakLabel"
                position="top"
                fontSize={11}
                className="fill-foreground"
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
