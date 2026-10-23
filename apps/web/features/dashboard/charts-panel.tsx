"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { dashboardApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/query-keys";
import { Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { DrawdownChart, LineSeriesChart, SignedBarChart } from "@/components/common/charts";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { GenericStatusBadge } from "@/components/common/labels";
import { useTimeFormat } from "@/hooks/use-auth";
import { dayEndIso, dayStartIso, daysAgo, isoDay } from "@/lib/time";
import { fmtSigned, pnlClass } from "@/lib/utils";
import type { DashboardCharts } from "@/types/api";

const PRESETS: { label: string; days: number | null }[] = [
  { label: "7D", days: 7 },
  { label: "30D", days: 30 },
  { label: "90D", days: 90 },
  { label: "All", days: null },
];

export function useDateRange(initialDays: number | null = 30) {
  const [start, setStart] = React.useState(initialDays ? isoDay(daysAgo(initialDays)) : "");
  const [end, setEnd] = React.useState("");
  return { start, end, setStart, setEnd };
}

export function DateRangeFilter({
  start,
  end,
  setStart,
  setEnd,
}: ReturnType<typeof useDateRange>) {
  return (
    <div className="flex flex-wrap items-end gap-2" role="group" aria-label="Date range">
      {PRESETS.map((p) => (
        <Button
          key={p.label}
          size="xs"
          variant="outline"
          onClick={() => {
            setStart(p.days ? isoDay(daysAgo(p.days)) : "");
            setEnd("");
          }}
        >
          {p.label}
        </Button>
      ))}
      <div className="flex items-center gap-1">
        <Label htmlFor="range-start" className="sr-only">
          Start date
        </Label>
        <Input id="range-start" type="date" className="h-7 w-36 text-xs" value={start} onChange={(e) => setStart(e.target.value)} />
        <span className="text-xs text-muted-foreground">to</span>
        <Label htmlFor="range-end" className="sr-only">
          End date
        </Label>
        <Input id="range-end" type="date" className="h-7 w-36 text-xs" value={end} onChange={(e) => setEnd(e.target.value)} />
      </div>
    </div>
  );
}

export function useDashboardCharts(start: string, end: string) {
  const params = { start: dayStartIso(start) || undefined, end: dayEndIso(end) || undefined };
  return useQuery({ queryKey: qk.dashboardCharts(params), queryFn: () => dashboardApi.charts(params) });
}

export function DashboardChartsPanel() {
  const range = useDateRange(30);
  const charts = useDashboardCharts(range.start, range.end);
  const t = useTimeFormat();

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Performance charts</h2>
        <DateRangeFilter {...range} />
      </div>
      {charts.isLoading ? (
        <LoadingBlock rows={6} />
      ) : charts.error ? (
        <ErrorState error={charts.error} onRetry={() => void charts.refetch()} />
      ) : charts.data ? (
        <ChartsGrid data={charts.data} dt={t.dt} date={t.date} />
      ) : null}
    </div>
  );
}

export function ChartsGrid({
  data,
  dt,
  date,
}: {
  data: DashboardCharts;
  dt: (s: string) => string;
  date: (s: string) => string;
}) {
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <Section title="Equity curve">
        <LineSeriesChart
          data={data.equity}
          xKey="time"
          series={[{ key: "equity", name: "Equity" }]}
          label="Equity curve"
          xFormatter={(v) => date(String(v))}
          emptyText="No equity history in this range"
        />
      </Section>
      <Section title="Balance">
        <LineSeriesChart
          data={data.equity}
          xKey="time"
          series={[{ key: "balance", name: "Balance" }]}
          label="Balance"
          xFormatter={(v) => date(String(v))}
          emptyText="No balance history in this range"
        />
      </Section>
      <Section title="Daily realized P/L">
        <SignedBarChart data={data.daily_pnl} xKey="date" yKey="pnl" label="Daily realized P/L" emptyText="No closed trades in this range" />
      </Section>
      <Section title="Drawdown">
        <DrawdownChart data={data.drawdown} xKey="time" yKey="drawdown_pct" label="Drawdown" xFormatter={(v) => dt(String(v))} />
      </Section>
      <Section title="Performance by bot">
        <SignedBarChart
          data={data.by_bot}
          xKey="name"
          yKey="net_pnl"
          layout="vertical"
          label="Net P/L by bot"
          height={Math.max(160, data.by_bot.length * 36)}
          emptyText="No bot results in this range"
        />
      </Section>
      <Section title="Performance by instrument">
        <SignedBarChart
          data={data.by_instrument}
          xKey="symbol"
          yKey="net_pnl"
          layout="vertical"
          label="Net P/L by instrument"
          height={Math.max(160, data.by_instrument.length * 36)}
          emptyText="No instrument results in this range"
        />
      </Section>
      <Section title="Recent trade outcomes" className="xl:col-span-2">
        {data.recent_outcomes.length === 0 ? (
          <EmptyState title="No completed cycles in this range" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Closed</TH>
                <TH>Cycle</TH>
                <TH>Bot</TH>
                <TH>Symbol</TH>
                <TH>Outcome</TH>
                <TH className="text-right">Net P/L</TH>
              </TR>
            </THead>
            <TBody>
              {data.recent_outcomes.map((o) => (
                <TR key={o.cycle_id}>
                  <TD className="num text-xs">{dt(o.closed_at)}</TD>
                  <TD>
                    <Link href={`/cycles/${encodeURIComponent(o.cycle_id)}`} className="num text-xs text-primary hover:underline">
                      {o.cycle_id.slice(0, 8)}
                    </Link>
                  </TD>
                  <TD>{o.bot_name}</TD>
                  <TD>{o.symbol}</TD>
                  <TD>
                    <GenericStatusBadge status={o.outcome} />
                  </TD>
                  <TD className={`text-right num ${pnlClass(o.net_pnl)}`}>{fmtSigned(o.net_pnl)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Section>
    </div>
  );
}
