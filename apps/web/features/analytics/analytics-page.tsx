"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { tradingApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/query-keys";
import { PageHeader, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { SignedBarChart } from "@/components/common/charts";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useTimeFormat } from "@/hooks/use-auth";
import { dayEndIso, dayStartIso } from "@/lib/time";
import { fmtNumber, fmtSigned, pnlClass } from "@/lib/utils";
import { DateRangeFilter, useDashboardCharts, useDateRange } from "@/features/dashboard/charts-panel";

/** Groups the backend's daily realized P/L into calendar months (display aggregation only). */
export function monthlyFromDaily(daily: { date: string; pnl: number }[]) {
  const map = new Map<string, { month: string; pnl: number; days: number }>();
  for (const d of daily) {
    const m = d.date.slice(0, 7);
    const row = map.get(m) ?? { month: m, pnl: 0, days: 0 };
    row.pnl += d.pnl;
    row.days += 1;
    map.set(m, row);
  }
  return Array.from(map.values()).sort((a, b) => a.month.localeCompare(b.month));
}

function PnlTable({ rows, label, countLabel }: { rows: { name: string; pnl: number; count: number }[]; label: string; countLabel: string }) {
  if (!rows.length) return <EmptyState title="No data in this range" />;
  return (
    <Table>
      <THead>
        <TR>
          <TH>{label}</TH>
          <TH className="text-right">{countLabel}</TH>
          <TH className="text-right">Net P/L</TH>
        </TR>
      </THead>
      <TBody>
        {rows.map((r) => (
          <TR key={r.name}>
            <TD>{r.name}</TD>
            <TD className="text-right num">{fmtNumber(r.count, 0)}</TD>
            <TD className={`text-right num ${pnlClass(r.pnl)}`}>{fmtSigned(r.pnl)}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

export function AnalyticsPage() {
  const range = useDateRange(90);
  const charts = useDashboardCharts(range.start, range.end);
  const t = useTimeFormat();
  const tradeFilters = { start: dayStartIso(range.start) || undefined, end: dayEndIso(range.end) || undefined, outcome: "loss" as const, page: 1, page_size: 10, sort: "net_pnl" };
  const worst = useQuery({ queryKey: qk.trades(tradeFilters), queryFn: () => tradingApi.trades(tradeFilters) });
  const monthly = charts.data ? monthlyFromDaily(charts.data.daily_pnl) : [];

  return (
    <div className="space-y-3">
      <PageHeader title="Analytics" description="Performance breakdowns from backend-computed results." actions={<DateRangeFilter {...range} />} />
      {charts.isLoading ? (
        <LoadingBlock rows={8} />
      ) : charts.error ? (
        <ErrorState error={charts.error} onRetry={() => void charts.refetch()} />
      ) : charts.data ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <Section title="By bot">
            <SignedBarChart data={charts.data.by_bot} xKey="name" yKey="net_pnl" layout="vertical" label="Net P/L by bot" height={Math.max(160, charts.data.by_bot.length * 36)} />
            <PnlTable rows={charts.data.by_bot.map((b) => ({ name: b.name, pnl: b.net_pnl, count: b.cycles }))} label="Bot" countLabel="Cycles" />
          </Section>
          <Section title="By instrument">
            <SignedBarChart data={charts.data.by_instrument} xKey="symbol" yKey="net_pnl" layout="vertical" label="Net P/L by instrument" height={Math.max(160, charts.data.by_instrument.length * 36)} />
            <PnlTable rows={charts.data.by_instrument.map((b) => ({ name: b.symbol, pnl: b.net_pnl, count: b.trades }))} label="Instrument" countLabel="Trades" />
          </Section>
          <Section title="By month (sum of daily realized P/L)" className="xl:col-span-2">
            <SignedBarChart data={monthly} xKey="month" yKey="pnl" label="Realized P/L by month" />
            <PnlTable rows={monthly.map((m) => ({ name: m.month, pnl: m.pnl, count: m.days }))} label="Month" countLabel="Trading days" />
          </Section>
        </div>
      ) : null}
      <Section title="Largest losing trades in range">
        {worst.isLoading ? (
          <LoadingBlock rows={4} />
        ) : worst.error ? (
          <ErrorState error={worst.error} />
        ) : !worst.data?.items.length ? (
          <EmptyState title="No losing trades in this range" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Exit</TH>
                <TH>Bot</TH>
                <TH>Symbol</TH>
                <TH>Exit reason</TH>
                <TH className="text-right">Net P/L</TH>
              </TR>
            </THead>
            <TBody>
              {worst.data.items.map((r) => (
                <TR key={r.id}>
                  <TD className="num text-xs">{t.dt(r.exit_time)}</TD>
                  <TD>{r.bot_name}</TD>
                  <TD>{r.symbol}</TD>
                  <TD className="text-xs">{r.exit_reason ?? "—"}</TD>
                  <TD className={`text-right num ${pnlClass(r.net_pnl)}`}>{fmtSigned(r.net_pnl)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Section>
    </div>
  );
}
