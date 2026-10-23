"use client";
import * as React from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EmptyState } from "./states";
import { fmtNumber } from "@/lib/utils";

/** Categorical slots (validated for the dark surface) — assigned in fixed order. */
export const SERIES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];
const PROFIT = "var(--color-profit)";
const LOSS = "var(--color-loss)";
const AXIS = { stroke: "var(--color-muted-foreground)", fontSize: 11 };
const GRID = "var(--color-border)";

const tooltipStyle: React.CSSProperties = {
  background: "var(--color-surface-2)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  fontSize: 12,
  color: "var(--color-foreground)",
};

type Fmt = (v: string | number) => string;

function ChartFrame({ height, children, label }: { height: number; children: React.ReactElement; label: string }) {
  return (
    <figure aria-label={label} style={{ width: "100%", height }} className="m-0">
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </figure>
  );
}

export interface SeriesDef {
  key: string;
  name: string;
}

export function LineSeriesChart<T extends object>({
  data,
  xKey,
  series,
  label,
  height = 240,
  xFormatter,
  emptyText = "No data for this range",
}: {
  data: T[];
  xKey: keyof T & string;
  series: SeriesDef[];
  label: string;
  height?: number;
  xFormatter?: Fmt;
  emptyText?: string;
}) {
  if (!data.length) return <EmptyState title={emptyText} className="py-10" />;
  return (
    <ChartFrame height={height} label={label}>
      <LineChart data={data as unknown as Record<string, unknown>[]} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey={xKey as string} tick={AXIS} tickFormatter={xFormatter} minTickGap={32} stroke={GRID} />
        <YAxis tick={AXIS} width={64} domain={["auto", "auto"]} tickFormatter={(v: number) => fmtNumber(v, 0)} stroke={GRID} />
        <Tooltip
          contentStyle={tooltipStyle}
          labelFormatter={(l) => (xFormatter ? xFormatter(l as string) : String(l))}
          formatter={(v) => fmtNumber(Number(v), 2)}
        />
        {series.length > 1 ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null}
        {series.map((s, i) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.name}
            stroke={SERIES[i % SERIES.length]}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartFrame>
  );
}

/** Bars coloured by sign (profit/loss). */
export function SignedBarChart<T extends object>({
  data,
  xKey,
  yKey,
  label,
  height = 220,
  xFormatter,
  layout = "horizontal",
  emptyText = "No data for this range",
}: {
  data: T[];
  xKey: keyof T & string;
  yKey: keyof T & string;
  label: string;
  height?: number;
  xFormatter?: Fmt;
  layout?: "horizontal" | "vertical";
  emptyText?: string;
}) {
  if (!data.length) return <EmptyState title={emptyText} className="py-10" />;
  const vertical = layout === "vertical";
  return (
    <ChartFrame height={height} label={label}>
      <BarChart data={data as unknown as Record<string, unknown>[]} layout={layout} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={vertical} horizontal={!vertical} />
        {vertical ? (
          <>
            <XAxis type="number" tick={AXIS} tickFormatter={(v: number) => fmtNumber(v, 0)} stroke={GRID} />
            <YAxis type="category" dataKey={xKey as string} tick={AXIS} width={110} stroke={GRID} />
          </>
        ) : (
          <>
            <XAxis dataKey={xKey as string} tick={AXIS} tickFormatter={xFormatter} minTickGap={16} stroke={GRID} />
            <YAxis tick={AXIS} width={64} tickFormatter={(v: number) => fmtNumber(v, 0)} stroke={GRID} />
          </>
        )}
        <Tooltip
          contentStyle={tooltipStyle}
          cursor={{ fill: "var(--color-muted)", opacity: 0.4 }}
          formatter={(v) => fmtNumber(Number(v), 2)}
        />
        {vertical ? <ReferenceLine x={0} stroke="var(--color-muted-foreground)" /> : <ReferenceLine y={0} stroke="var(--color-muted-foreground)" />}
        <Bar dataKey={yKey as string} name={label} radius={vertical ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={false} maxBarSize={28}>
          {data.map((d, i) => {
            const v = Number((d as Record<string, unknown>)[yKey]);
            return <Cell key={i} fill={v >= 0 ? PROFIT : LOSS} />;
          })}
        </Bar>
      </BarChart>
    </ChartFrame>
  );
}

export function DrawdownChart<T extends object>({
  data,
  xKey,
  yKey,
  label,
  height = 200,
  xFormatter,
}: {
  data: T[];
  xKey: keyof T & string;
  yKey: keyof T & string;
  label: string;
  height?: number;
  xFormatter?: Fmt;
}) {
  if (!data.length) return <EmptyState title="No drawdown data for this range" className="py-10" />;
  // Drawdown is plotted as a negative percentage below zero.
  const plotted = data.map((d) => {
    const v = Number((d as Record<string, unknown>)[yKey]);
    return { ...d, __dd: Number.isFinite(v) ? -Math.abs(v) : null };
  });
  return (
    <ChartFrame height={height} label={label}>
      <AreaChart data={plotted as unknown as Record<string, unknown>[]} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey={xKey as string} tick={AXIS} tickFormatter={xFormatter} minTickGap={32} stroke={GRID} />
        <YAxis tick={AXIS} width={64} tickFormatter={(v: number) => `${fmtNumber(v, 1)}%`} stroke={GRID} />
        <Tooltip
          contentStyle={tooltipStyle}
          labelFormatter={(l) => (xFormatter ? xFormatter(l as string) : String(l))}
          formatter={(v) => `${fmtNumber(Number(v), 2)}%`}
        />
        <Area
          type="monotone"
          dataKey="__dd"
          name="Drawdown"
          stroke={LOSS}
          fill={LOSS}
          fillOpacity={0.18}
          strokeWidth={2}
          isAnimationActive={false}
        />
      </AreaChart>
    </ChartFrame>
  );
}
