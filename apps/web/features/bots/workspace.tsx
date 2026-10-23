"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, OctagonX } from "lucide-react";
import { botsApi, tradingApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { BOT_IDENTITIES, WORKSPACE_TABS, botIdentity, workspaceHref } from "@/lib/bots";
import { Section, StatCard } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { BotStatusBadge, EnvironmentBadge } from "@/components/common/labels";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { useSystemStatus } from "@/hooks/use-system-status";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { cn, fmtPct, fmtSigned, pnlClass } from "@/lib/utils";
import { BotControls } from "./bot-controls";
import { BotConfigTab } from "./bot-config-tab";
import { ShutdownDialog } from "./shutdown-dialog";
import { EvaluateNowPanel, EvaluationsTab, ManualEntryPanel, PerformanceTab, PositionsTab } from "./bot-detail";
import { TradeHistory } from "@/features/trades/trade-history";
import { CyclesTable } from "@/features/trades/cycles-table";
import { RunsList } from "@/features/backtests/backtests-page";
import { BacktestForm } from "@/features/backtests/backtest-form";

/** Header + tab bar shared by every page of one bot's workspace. */
export function BotWorkspaceShell({ botId, children }: { botId: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const bot = useQuery({ queryKey: qk.bot(botId), queryFn: () => botsApi.get(botId), refetchInterval: REFETCH.live });
  const status = useSystemStatus();
  const { isAdmin } = useSession();
  const [shutdownOpen, setShutdownOpen] = React.useState(false);
  const id = botIdentity(botId);
  const Icon = id.icon;
  const live = status.data?.environment === "live";
  const other = BOT_IDENTITIES.find((b) => b.id !== id.id);
  const sub = pathname.replace(workspaceHref(id.id), "").replace(/^\//, "").split("/")[0] ?? "";

  if (bot.isLoading) return <LoadingBlock rows={8} />;
  if (bot.error) return <ErrorState error={bot.error} onRetry={() => void bot.refetch()} />;
  if (!bot.data) return null;
  const b = bot.data;

  return (
    <div className="space-y-4" data-workspace={id.id}>
      <section
        aria-label={`${id.short} workspace`}
        className={cn("rounded-lg border border-l-4 border-border bg-surface", id.border)}
      >
        <div className={cn("flex flex-wrap items-start justify-between gap-3 rounded-t-lg p-3 sm:p-4", id.panel)}>
          <div className="flex min-w-0 items-start gap-3">
            <span className={cn("grid size-10 shrink-0 place-items-center rounded-lg border", id.chip)}>
              <Icon className="size-5" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className={cn("text-[11px] font-semibold uppercase tracking-wider", id.text)}>Bot workspace</p>
              <h1 className="text-lg font-semibold leading-tight tracking-tight">{b.name}</h1>
              <p className="mt-0.5 text-xs text-muted-foreground">{id.tagline}</p>
              <p className="mt-1 text-xs text-muted-foreground num">
                {b.strategy_name} v{b.strategy_version} · {b.instruments.join(", ")} · max risk {fmtPct(b.max_risk_pct)}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <EnvironmentBadge env={b.environment} />
            <BotStatusBadge status={b.status} />
            {other ? (
              <Button asChild size="xs" variant="ghost" title={`Switch to the ${other.short} workspace`}>
                <Link href={workspaceHref(other.id, sub)}>
                  <ArrowLeftRight />
                  <span className="hidden sm:inline">Switch to {other.short}</span>
                  <span className="sm:hidden">Switch bot</span>
                </Link>
              </Button>
            ) : null}
          </div>
        </div>
        {b.last_error ? (
          <div className="px-3 pt-3 sm:px-4">
            <Alert variant="danger">Last error: {b.last_error}</Alert>
          </div>
        ) : null}
        {isAdmin ? (
          <div className="flex flex-wrap items-end justify-between gap-2 border-t border-border px-3 py-2.5 sm:px-4">
            <BotControls bot={b} liveEnvironment={live} />
            <Button size="xs" variant="outline" onClick={() => setShutdownOpen(true)}>
              <OctagonX />
              Orderly shutdown
            </Button>
            <ShutdownDialog bot={b} open={shutdownOpen} onOpenChange={setShutdownOpen} />
          </div>
        ) : null}
        <nav aria-label={`${id.short} sections`} className="border-t border-border">
          <ul className="flex overflow-x-auto px-1 [scrollbar-width:none]">
            {WORKSPACE_TABS.map((tab) => {
              const active = sub === tab.slug;
              return (
                <li key={tab.slug} className="shrink-0">
                  <Link
                    href={workspaceHref(id.id, tab.slug)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "-mb-px flex border-b-2 px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground",
                      active ? cn("border-current font-medium", id.text) : "border-transparent",
                    )}
                  >
                    {tab.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </section>
      {children}
    </div>
  );
}

export function WorkspaceOverview({ botId }: { botId: string }) {
  const t = useTimeFormat();
  const bot = useQuery({ queryKey: qk.bot(botId), queryFn: () => botsApi.get(botId), refetchInterval: REFETCH.live });
  const activeFilters = { bot_id: botId, active: true, page: 1, page_size: 20 };
  const active = useQuery({ queryKey: qk.cycles(activeFilters), queryFn: () => tradingApi.cycles(activeFilters), refetchInterval: REFETCH.live });
  const b = bot.data;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Status" value={b ? <BotStatusBadge status={b.status} /> : "—"} />
        <StatCard label="Daily P/L" value={fmtSigned(b?.daily_pnl)} valueClassName={pnlClass(b?.daily_pnl)} />
        <StatCard label="Lifetime P/L" value={fmtSigned(b?.lifetime_pnl)} valueClassName={pnlClass(b?.lifetime_pnl)} />
        <StatCard label="Active cycles" value={b?.active_cycles ?? "—"} />
        <StatCard label="Open positions" value={b?.open_positions ?? "—"} />
        <StatCard label="Risk per entry" value={fmtPct(b?.max_risk_pct)} hint="after risk-engine caps" />
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Section title="Activity" className="lg:col-span-1">
          <dl className="space-y-1.5 text-sm">
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">Last evaluation</dt>
              <dd className="num text-xs">{t.dt(b?.last_evaluation_at)}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">Last execution</dt>
              <dd className="num text-xs">{t.dt(b?.last_execution_at)}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">Timeframe</dt>
              <dd className="num">{b?.timeframe ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">Config version</dt>
              <dd className="num">v{b?.config_version ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">Broker magic</dt>
              <dd className="num">{b?.magic ?? "—"}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            The magic number tags every order this bot sends, so the platform can never mix its positions with the other bot.
          </p>
        </Section>
        <Section title="Active cycles" className="lg:col-span-2">
          {active.isLoading ? (
            <LoadingBlock rows={3} />
          ) : active.error ? (
            <ErrorState error={active.error} />
          ) : (active.data?.items.length ?? 0) === 0 ? (
            <EmptyState title="No active cycles" description="Start the bot, or place a manual paper entry from the Activity tab." />
          ) : (
            <CyclesTable cycles={active.data?.items ?? []} />
          )}
        </Section>
      </div>
      <PerformanceTab botId={botId} showStats={false} />
    </div>
  );
}

export function WorkspacePositions({ botId }: { botId: string }) {
  return <PositionsTab botId={botId} />;
}

export function WorkspaceTrades({ botId }: { botId: string }) {
  return <TradeHistory botId={botId} />;
}

export function WorkspaceBacktests({ botId }: { botId: string }) {
  const router = useRouter();
  const { isAdmin } = useSession();
  const id = botIdentity(botId);
  return (
    <div className="space-y-3">
      {isAdmin ? (
        <Section title={`New ${id.short} backtest`}>
          <BacktestForm
            initialBotKind={id.kind}
            lockBotKind
            onCreated={(run) => router.push(`/backtests/${encodeURIComponent(run.id)}`)}
          />
        </Section>
      ) : null}
      <Section title={`${id.short} runs`}>
        <RunsList botKind={id.kind} />
      </Section>
    </div>
  );
}

export function WorkspaceConfiguration({ botId }: { botId: string }) {
  return <BotConfigTab botId={botId} />;
}

export function WorkspaceActivity({ botId }: { botId: string }) {
  const bot = useQuery({ queryKey: qk.bot(botId), queryFn: () => botsApi.get(botId) });
  const status = useSystemStatus();
  const { isAdmin } = useSession();
  const live = status.data?.environment === "live" || bot.data?.environment === "live";
  const viewer = !isAdmin ? "Viewers cannot trigger evaluations or entries." : null;
  const evalDisabled = viewer ?? (live ? "Not available in live trading." : null);
  return (
    <div className="space-y-4">
      <EvaluateNowPanel botId={botId} disabledReason={evalDisabled} />
      {live && !viewer ? (
        <Alert variant="warning">
          Live mode: a manual entry sends a real order to the broker. The server only allows it when the connected account is a
          DEMO account; real-money accounts are refused.
        </Alert>
      ) : null}
      {bot.data ? <ManualEntryPanel bot={bot.data} disabledReason={viewer} /> : null}
      <EvaluationsTab botId={botId} />
    </div>
  );
}
