"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, FlaskConical, OctagonX } from "lucide-react";
import { botIdentity, workspaceHref } from "@/lib/bots";
import { botsApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { PageHeader } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { BotStatusBadge, EnvironmentBadge } from "@/components/common/labels";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useSystemStatus } from "@/hooks/use-system-status";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { cn, fmtPct, fmtSigned, humanize, pnlClass } from "@/lib/utils";
import type { Bot } from "@/types/api";
import { BotControls } from "./bot-controls";
import { ShutdownDialog } from "./shutdown-dialog";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="truncate text-sm num">{children}</dd>
    </div>
  );
}

export function BotCard({ bot, liveEnvironment }: { bot: Bot; liveEnvironment: boolean }) {
  const t = useTimeFormat();
  const { isAdmin } = useSession();
  const [shutdownOpen, setShutdownOpen] = React.useState(false);
  const identity = botIdentity(bot.id);
  const BotIcon = identity.icon;
  return (
    <Card className={cn("flex flex-col gap-3 border-l-4 p-4", identity.border)} data-testid={`bot-card-${bot.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg border", identity.chip)}>
            <BotIcon className="size-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <Link href={workspaceHref(bot.id)} className="text-base font-semibold hover:underline">
              {bot.name}
            </Link>
            <p className="text-xs text-muted-foreground">
              {humanize(bot.kind)} · {bot.strategy_name} v{bot.strategy_version}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <EnvironmentBadge env={bot.environment} />
          <BotStatusBadge status={bot.status} />
          {!bot.enabled ? <Badge variant="muted">Disabled</Badge> : null}
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <Field label="Instruments">{bot.instruments.join(", ") || "—"}</Field>
        <Field label="Timeframe">{bot.timeframe ?? "—"}</Field>
        <Field label="Max risk">{fmtPct(bot.max_risk_pct)}</Field>
        <Field label="Magic">{bot.magic ?? "—"}</Field>
        <Field label="Active cycles">{bot.active_cycles}</Field>
        <Field label="Open positions">{bot.open_positions}</Field>
        <Field label="Daily P/L">
          <span className={pnlClass(bot.daily_pnl)}>{fmtSigned(bot.daily_pnl)}</span>
        </Field>
        <Field label="Lifetime P/L">
          <span className={pnlClass(bot.lifetime_pnl)}>{fmtSigned(bot.lifetime_pnl)}</span>
        </Field>
        <Field label="Last evaluation">{t.dt(bot.last_evaluation_at)}</Field>
        <Field label="Last execution">{t.dt(bot.last_execution_at)}</Field>
        <Field label="Config version">{bot.config_version ?? "—"}</Field>
        <Field label="Environment">{humanize(bot.environment)}</Field>
      </dl>
      {bot.last_error ? (
        <p role="alert" className="rounded-md border border-loss/40 bg-loss/5 px-2 py-1 text-xs text-loss">
          Last error: {bot.last_error}
        </p>
      ) : null}
      <div className="mt-auto flex flex-wrap items-end justify-between gap-2 border-t border-border pt-3">
        {isAdmin ? <BotControls bot={bot} liveEnvironment={liveEnvironment} /> : <span className="text-xs text-muted-foreground">Read-only (viewer)</span>}
        <div className="flex flex-wrap gap-1.5">
          <Button asChild size="xs" variant="secondary">
            <Link href={workspaceHref(bot.id)}>
              Open workspace
              <ArrowRight />
            </Link>
          </Button>
          <Button asChild size="xs" variant="ghost">
            <Link href={workspaceHref(bot.id, "backtests")}>
              <FlaskConical />
              Backtest
            </Link>
          </Button>
          {isAdmin ? (
            <Button size="xs" variant="outline" onClick={() => setShutdownOpen(true)}>
              <OctagonX />
              Orderly shutdown
            </Button>
          ) : null}
        </div>
      </div>
      <ShutdownDialog bot={bot} open={shutdownOpen} onOpenChange={setShutdownOpen} />
    </Card>
  );
}

export function BotsPage() {
  const bots = useQuery({ queryKey: qk.bots, queryFn: botsApi.list, refetchInterval: REFETCH.live });
  const status = useSystemStatus();
  const live = status.data?.environment === "live";
  return (
    <div>
      <PageHeader
        title="Trading Bots"
        description="Both bots side by side. Open a bot's workspace to work on it alone; status changes only after the server confirms them."
      />
      {bots.isLoading ? (
        <LoadingBlock rows={6} />
      ) : bots.error ? (
        <ErrorState error={bots.error} onRetry={() => void bots.refetch()} />
      ) : bots.data && bots.data.length === 0 ? (
        <EmptyState title="No bots configured" description="Bots are defined by the backend." />
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {bots.data?.map((b) => <BotCard key={b.id} bot={b} liveEnvironment={live} />)}
        </div>
      )}
    </div>
  );
}
