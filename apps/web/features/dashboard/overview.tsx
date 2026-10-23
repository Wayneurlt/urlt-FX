"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { dashboardApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { PageHeader, Section, StatCard } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import {
  BotStatusBadge,
  DataSourceBadge,
  GenericStatusBadge,
  HealthBadge,
  OriginBadge,
  SeverityBadge,
  SideLabel,
  SimulatedTag,
} from "@/components/common/labels";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useTimeFormat } from "@/hooks/use-auth";
import { cn, fmtMoney, fmtNumber, fmtPct, fmtPrice, fmtRatioPct, fmtSigned, humanize, isNum, pnlClass } from "@/lib/utils";
import type { DashboardSummary, PerformanceSummary } from "@/types/api";
import { botIdentity, workspaceHref } from "@/lib/bots";
import { DashboardChartsPanel } from "./charts-panel";

const PERF_FIELDS: { key: keyof Omit<PerformanceSummary, "insufficient">; label: string; kind: "money" | "pct" | "ratio" | "num" | "int" }[] = [
  { key: "today_pnl", label: "Today P/L", kind: "money" },
  { key: "week_pnl", label: "Week P/L", kind: "money" },
  { key: "month_pnl", label: "Month P/L", kind: "money" },
  { key: "total_return_pct", label: "Total return", kind: "pct" },
  { key: "max_drawdown_pct", label: "Max drawdown", kind: "pct" },
  { key: "win_rate", label: "Win rate", kind: "ratio" },
  { key: "profit_factor", label: "Profit factor", kind: "num" },
  { key: "average_trade", label: "Average trade", kind: "money" },
  { key: "completed_trades", label: "Completed trades", kind: "int" },
  { key: "completed_cycles", label: "Completed cycles", kind: "int" },
];

export function PerformanceCards({ perf, currency }: { perf: PerformanceSummary; currency: string }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {PERF_FIELDS.map((f) => {
        const v = perf[f.key];
        const insufficient = !isNum(v) || perf.insufficient?.includes(f.key);
        let display: string;
        if (insufficient) display = "Not enough history";
        else if (f.kind === "money") display = fmtSigned(v, 2);
        else if (f.kind === "pct") display = fmtPct(v);
        else if (f.kind === "ratio") display = fmtRatioPct(v);
        else if (f.kind === "int") display = fmtNumber(v, 0);
        else display = fmtNumber(v, 2);
        return (
          <StatCard
            key={f.key}
            label={f.label}
            value={display}
            hint={!insufficient && f.kind === "money" ? currency : undefined}
            valueClassName={insufficient ? "text-sm font-normal text-muted-foreground" : f.kind === "money" ? pnlClass(v) : undefined}
          />
        );
      })}
    </div>
  );
}

export function AccountCards({ summary }: { summary: DashboardSummary }) {
  const a = summary.account;
  const tag = (
    <span className="flex gap-1">
      <SimulatedTag simulated={a.simulated} />
    </span>
  );
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>Account{a.label ? `: ${a.label}` : ""}</span>
        <SimulatedTag simulated={a.simulated} />
        <DataSourceBadge source={a.data_source} />
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Balance" value={fmtMoney(a.balance, a.currency)} tag={tag} />
        <StatCard label="Equity" value={fmtMoney(a.equity, a.currency)} tag={tag} />
        <StatCard label="Floating P/L" value={fmtSigned(a.floating_pnl)} valueClassName={pnlClass(a.floating_pnl)} hint={a.currency} />
        <StatCard label="Daily P/L" value={fmtSigned(a.daily_pnl)} valueClassName={pnlClass(a.daily_pnl)} hint={a.currency} />
        <StatCard label="Drawdown" value={fmtPct(a.drawdown_pct)} />
        <StatCard label="Free margin" value={fmtMoney(a.free_margin, a.currency)} hint={a.margin_level !== null ? `Margin level ${fmtPct(a.margin_level, 0)}` : undefined} />
      </div>
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span>
          Open positions: <Link className="num text-foreground hover:underline" href="/positions">{summary.open_positions}</Link>
        </span>
        <span>
          Active cycles: <span className="num text-foreground">{summary.active_cycles}</span>
        </span>
        <span>
          Realized P/L: <span className={`num ${pnlClass(a.realized_pnl)}`}>{fmtSigned(a.realized_pnl)}</span>
        </span>
      </div>
    </div>
  );
}

/** One card per bot: the entry point into that bot's own workspace. */
export function BotWorkspaceCards({ bots }: { bots: DashboardSummary["bots"] }) {
  if (bots.length === 0) return <EmptyState title="No bots configured" />;
  return (
    <section aria-label="Bot workspaces" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {bots.map((b) => {
        const id = botIdentity(b.id);
        const Icon = id.icon;
        return (
          <Link
            key={b.id}
            href={workspaceHref(id.id)}
            className={cn(
              "group flex min-w-0 items-center gap-3 rounded-lg border border-l-4 border-border bg-surface p-3 hover:bg-muted/40",
              id.border,
            )}
          >
            <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg border", id.chip)}>
              <Icon className="size-4" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="truncate font-semibold">{b.name}</span>
                <BotStatusBadge status={b.status} />
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                Today <span className={cn("num", pnlClass(b.daily_pnl))}>{fmtSigned(b.daily_pnl)}</span>
              </span>
            </span>
            <span className={cn("flex shrink-0 items-center gap-1 text-xs font-medium", id.text)}>
              <span className="hidden sm:inline">Open workspace</span>
              <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
            </span>
          </Link>
        );
      })}
    </section>
  );
}

function OverviewSkeleton() {
  return (
    <div className="space-y-3" role="status" aria-label="Loading overview">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

export function Overview() {
  const summary = useQuery({ queryKey: qk.dashboardSummary, queryFn: dashboardApi.summary, refetchInterval: REFETCH.live });
  const t = useTimeFormat();

  return (
    <div className="space-y-4">
      <PageHeader title="Dashboard" description="Portfolio view across both bots: account, performance and active operations." />
      {summary.isLoading ? (
        <OverviewSkeleton />
      ) : summary.error ? (
        <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
      ) : summary.data ? (
        <>
          <BotWorkspaceCards bots={summary.data.bots} />
          <AccountCards summary={summary.data} />
          <PerformanceCards perf={summary.data.performance} currency={summary.data.account.currency} />
        </>
      ) : null}

      <DashboardChartsPanel />

      {summary.data ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <Section title="Active risk breakers" actions={<Link href="/risk" className="text-xs text-primary hover:underline">Risk</Link>}>
            {summary.data.active_breakers.length === 0 ? (
              <EmptyState title="No active circuit breakers" />
            ) : (
              <ul className="space-y-2">
                {summary.data.active_breakers.map((b) => (
                  <li key={b.id} className="rounded-md border border-loss/40 bg-loss/5 p-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="danger">{humanize(b.type)}</Badge>
                      <span className="text-xs text-muted-foreground">
                        {b.scope}
                        {b.scope_key ? `: ${b.scope_key}` : ""}
                      </span>
                      {b.critical ? <Badge variant="danger">Critical</Badge> : null}
                      <span className="ml-auto text-xs text-muted-foreground num">{t.dt(b.activated_at)}</span>
                    </div>
                    <p className="mt-1">{b.reason}</p>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Recent executions & rejections">
            {summary.data.recent_executions.length === 0 ? (
              <EmptyState title="No recent executions" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Time</TH>
                    <TH>Bot</TH>
                    <TH>Symbol</TH>
                    <TH>Side</TH>
                    <TH>Kind</TH>
                    <TH className="text-right">Price</TH>
                    <TH className="text-right">Vol</TH>
                    <TH>Detail</TH>
                  </TR>
                </THead>
                <TBody>
                  {summary.data.recent_executions.map((e, i) => (
                    <TR key={`${e.time}-${i}`}>
                      <TD className="num text-xs">{t.dt(e.time, true)}</TD>
                      <TD>{e.bot_name ?? "—"}</TD>
                      <TD>
                        {e.cycle_id ? (
                          <Link href={`/cycles/${encodeURIComponent(e.cycle_id)}`} className="hover:underline">
                            {e.symbol}
                          </Link>
                        ) : (
                          e.symbol
                        )}
                      </TD>
                      <TD>
                        <SideLabel side={e.side} />
                      </TD>
                      <TD>
                        <span className="flex gap-1">
                          <GenericStatusBadge status={e.kind} />
                          <SimulatedTag simulated={e.simulated ?? summary.data.account.simulated} />
                        </span>
                      </TD>
                      <TD className="text-right num">{fmtPrice(e.price)}</TD>
                      <TD className="text-right num">{fmtNumber(e.volume, 2)}</TD>
                      <TD className="max-w-60 truncate text-xs text-muted-foreground" title={e.detail ?? undefined}>
                        {e.detail ?? ""}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Section>

          <Section title="Recent alerts" actions={<Link href="/notifications" className="text-xs text-primary hover:underline">All</Link>}>
            {summary.data.recent_alerts.length === 0 ? (
              <EmptyState title="No recent alerts" />
            ) : (
              <ul className="divide-y divide-border/60">
                {summary.data.recent_alerts.map((n) => (
                  <li key={n.id} className="flex items-start gap-2 py-1.5 text-sm">
                    <SeverityBadge severity={n.severity} />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{n.title}</p>
                      <p className="text-xs text-muted-foreground">{n.message}</p>
                    </div>
                    <span className="text-xs text-muted-foreground num">{t.dt(n.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Upcoming events" actions={<Link href="/market" className="text-xs text-primary hover:underline">Calendar</Link>}>
            {summary.data.upcoming_events.length === 0 ? (
              <EmptyState title="No upcoming events" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Time</TH>
                    <TH>Ccy</TH>
                    <TH>Event</TH>
                    <TH>Impact</TH>
                    <TH>Origin</TH>
                  </TR>
                </THead>
                <TBody>
                  {summary.data.upcoming_events.map((e) => (
                    <TR key={e.id}>
                      <TD className="num text-xs">{t.dt(e.scheduled_at)}</TD>
                      <TD>{e.currency}</TD>
                      <TD className="max-w-64 truncate">{e.name}</TD>
                      <TD>
                        <GenericStatusBadge status={e.impact} />
                      </TD>
                      <TD>
                        <OriginBadge origin={e.origin} />
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Section>

          <Section title="Health checks">
            {summary.data.health.length === 0 ? (
              <EmptyState title="No health checks reported" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Check</TH>
                    <TH>State</TH>
                    <TH>Detail</TH>
                    <TH>Last success</TH>
                  </TR>
                </THead>
                <TBody>
                  {summary.data.health.map((h) => (
                    <TR key={h.name}>
                      <TD>{humanize(h.name)}</TD>
                      <TD>
                        <HealthBadge state={h.state} />
                      </TD>
                      <TD className="max-w-64 truncate text-xs text-muted-foreground" title={h.detail ?? undefined}>
                        {h.detail ?? ""}
                      </TD>
                      <TD className="num text-xs">{t.dt(h.last_success)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Section>
        </div>
      ) : summary.isLoading ? (
        <LoadingBlock rows={6} />
      ) : null}
    </div>
  );
}
