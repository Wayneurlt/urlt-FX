"use client";
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Zap } from "lucide-react";
import { botsApi, dashboardApi, tradingApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { JsonView, Pagination, Section, StatCard } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { GenericStatusBadge } from "@/components/common/labels";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useTimeFormat } from "@/hooks/use-auth";
import { errorMessage } from "@/lib/api/client";
import { fmtNumber, fmtSigned, humanize, pnlClass } from "@/lib/utils";
import { PositionsTable } from "@/features/positions/positions-table";
import { CyclesTable } from "@/features/trades/cycles-table";
import type { Bot } from "@/types/api";

/* Panels used by the bot workspace pages (features/bots/workspace.tsx). */

export function EvaluationsTab({ botId }: { botId: string }) {
  const [page, setPage] = React.useState(1);
  const t = useTimeFormat();
  const q = useQuery({ queryKey: qk.botEvaluations(botId, page), queryFn: () => botsApi.evaluations(botId, page) });
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data || q.data.items.length === 0) return <EmptyState title="No evaluations recorded yet" />;
  return (
    <Section title="Evaluation log">
      <Table>
        <THead>
          <TR>
            <TH>Time</TH>
            <TH>Symbol</TH>
            <TH>Mode</TH>
            <TH>Result</TH>
            <TH>Reason</TH>
            <TH>Details</TH>
          </TR>
        </THead>
        <TBody>
          {q.data.items.map((e, i) => (
            <TR key={`${e.time}-${i}`}>
              <TD className="num text-xs">{t.dt(e.time, true)}</TD>
              <TD>{e.symbol}</TD>
              <TD>{e.mode}</TD>
              <TD>
                <GenericStatusBadge status={e.result} />
              </TD>
              <TD className="max-w-80 truncate text-xs" title={e.reason ?? undefined}>
                {e.reason ?? ""}
              </TD>
              <TD>
                {e.details ? (
                  <details>
                    <summary className="cursor-pointer text-xs text-primary">View</summary>
                    <JsonView value={e.details} className="mt-1 max-h-48 w-80 whitespace-pre-wrap" />
                  </details>
                ) : null}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPageChange={setPage} />
    </Section>
  );
}

export function PositionsTab({ botId }: { botId: string }) {
  const positions = useQuery({ queryKey: qk.positions, queryFn: tradingApi.positions, refetchInterval: REFETCH.live });
  const filters = { bot_id: botId, active: true, page: 1, page_size: 50 };
  const cycles = useQuery({ queryKey: qk.cycles(filters), queryFn: () => tradingApi.cycles(filters), refetchInterval: REFETCH.live });
  return (
    <div className="space-y-3">
      <Section title="Open positions">
        {positions.isLoading ? (
          <LoadingBlock />
        ) : positions.error ? (
          <ErrorState error={positions.error} />
        ) : (
          <PositionsTable positions={(positions.data ?? []).filter((p) => p.bot_id === botId)} hideBot />
        )}
      </Section>
      <Section title="Active cycles">
        {cycles.isLoading ? <LoadingBlock /> : cycles.error ? <ErrorState error={cycles.error} /> : <CyclesTable cycles={cycles.data?.items ?? []} />}
      </Section>
    </div>
  );
}

export function PerformanceTab({ botId, showStats = true }: { botId: string; showStats?: boolean }) {
  const bot = useQuery({ queryKey: qk.bot(botId), queryFn: () => botsApi.get(botId) });
  const charts = useQuery({ queryKey: qk.dashboardCharts({}), queryFn: () => dashboardApi.charts({}) });
  const filters = { bot_id: botId, active: false, page: 1, page_size: 20 };
  const closed = useQuery({ queryKey: qk.cycles(filters), queryFn: () => tradingApi.cycles(filters) });
  const row = charts.data?.by_bot.find((b) => b.bot_id === botId);
  return (
    <div className="space-y-3">
      {showStats ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatCard label="Daily P/L" value={fmtSigned(bot.data?.daily_pnl)} valueClassName={pnlClass(bot.data?.daily_pnl)} />
          <StatCard label="Lifetime P/L" value={fmtSigned(bot.data?.lifetime_pnl)} valueClassName={pnlClass(bot.data?.lifetime_pnl)} />
          <StatCard
            label="Net P/L (all time, charts)"
            value={row ? fmtSigned(row.net_pnl) : "Not enough history"}
            valueClassName={row ? pnlClass(row.net_pnl) : "text-sm font-normal text-muted-foreground"}
          />
          <StatCard label="Completed cycles" value={row ? fmtNumber(row.cycles, 0) : "—"} />
        </div>
      ) : null}
      <Section title="Recent closed cycles">
        {closed.isLoading ? <LoadingBlock /> : closed.error ? <ErrorState error={closed.error} /> : <CyclesTable cycles={closed.data?.items ?? []} />}
      </Section>
    </div>
  );
}

export function EvaluateNowPanel({ botId, disabledReason }: { botId: string; disabledReason: string | null }) {
  const m = useMutation({ mutationFn: () => botsApi.evaluateNow(botId) });
  return (
    <Section
      title="Evaluate now (manual paper tick)"
      actions={
        <Button size="sm" onClick={() => m.mutate()} disabled={m.isPending || !!disabledReason} title={disabledReason ?? undefined}>
          <Zap />
          {m.isPending ? "Evaluating…" : "Evaluate now"}
        </Button>
      }
    >
      {disabledReason ? <p className="text-xs text-muted-foreground">{disabledReason}</p> : null}
      {m.error ? <Alert variant="danger">{errorMessage(m.error)}</Alert> : null}
      {m.data ? (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <div>
            <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Entries</p>
            {m.data.entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No entries were attempted.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {m.data.entries.map((e, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <GenericStatusBadge status={e.approved ? "approved" : "rejected"} />
                    {e.cycle_id ? (
                      <Link href={`/cycles/${encodeURIComponent(e.cycle_id)}`} className="num text-xs text-primary hover:underline">
                        {e.cycle_id.slice(0, 8)}
                      </Link>
                    ) : null}
                    <span className="text-xs text-muted-foreground">{e.reasons.join("; ")}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mb-1 mt-3 text-xs font-semibold uppercase text-muted-foreground">Blocked reasons</p>
            {m.data.blocked.length ? <JsonView value={m.data.blocked} /> : <p className="text-sm text-muted-foreground">None</p>}
          </div>
          <div className="space-y-3">
            <div>
              <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Signals</p>
              {m.data.signals.length ? <JsonView value={m.data.signals} /> : <p className="text-sm text-muted-foreground">No signals</p>}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Diagnostics</p>
              <JsonView value={m.data.diagnostics} />
            </div>
          </div>
        </div>
      ) : !m.isPending && !disabledReason ? (
        <p className="text-xs text-muted-foreground">Runs a single evaluation tick against current data. Simulation / paper only.</p>
      ) : null}
    </Section>
  );
}

interface ManualEntryResult {
  cycle_id: string | null;
  approved: boolean;
  reasons: string[];
  state?: string;
}

export function ManualEntryPanel({ bot, disabledReason }: { bot: Bot; disabledReason: string | null }) {
  const dual = bot.kind === "news_volatility";
  const [symbol, setSymbol] = React.useState(bot.instruments[0] ?? "EURUSD");
  const [side, setSide] = React.useState<"BUY" | "SELL">("BUY");
  const [stopPips, setStopPips] = React.useState("15");
  const [targetPips, setTargetPips] = React.useState("");
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () =>
      tradingApi.manualEntry({
        bot_id: bot.id,
        symbol,
        stop_pips: Number(stopPips),
        ...(dual ? {} : { side, ...(targetPips ? { target_pips: Number(targetPips) } : {}) }),
      }) as Promise<ManualEntryResult>,
    onSuccess: () => void qc.invalidateQueries(),
  });
  const stopValid = Number(stopPips) > 0;
  return (
    <Section title={dual ? "Manual paper entry (dual BUY + SELL)" : "Manual paper entry"}>
      <p className="mb-3 text-xs text-muted-foreground">
        Places a SIMULATED entry through the same risk engine and executor as automated trading. Simulation / paper only; the bot must
        be enabled and not paused or risk-halted.
      </p>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <div className="space-y-1">
          <Label htmlFor="me-symbol">Instrument</Label>
          <Select id="me-symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {bot.instruments.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
        {!dual ? (
          <div className="space-y-1">
            <Label htmlFor="me-side">Direction</Label>
            <Select id="me-side" value={side} onChange={(e) => setSide(e.target.value as "BUY" | "SELL")}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </Select>
          </div>
        ) : null}
        <div className="space-y-1">
          <Label htmlFor="me-stop">Stop (pips)</Label>
          <Input id="me-stop" inputMode="decimal" className="w-24" value={stopPips} onChange={(e) => setStopPips(e.target.value)} />
        </div>
        {!dual ? (
          <div className="space-y-1">
            <Label htmlFor="me-target">Target (pips, optional)</Label>
            <Input id="me-target" inputMode="decimal" className="w-24" value={targetPips} onChange={(e) => setTargetPips(e.target.value)} />
          </div>
        ) : null}
        <Button type="submit" size="sm" disabled={m.isPending || !!disabledReason || !stopValid} title={disabledReason ?? undefined}>
          {m.isPending ? "Submitting…" : "Submit paper entry"}
        </Button>
      </form>
      {disabledReason ? <p className="mt-2 text-xs text-muted-foreground">{disabledReason}</p> : null}
      {m.error ? <Alert variant="danger" className="mt-3">{errorMessage(m.error)}</Alert> : null}
      {m.data ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <GenericStatusBadge status={m.data.approved ? "approved" : "rejected"} />
          {m.data.cycle_id ? (
            <Link href={`/cycles/${encodeURIComponent(m.data.cycle_id)}`} className="num text-xs text-primary hover:underline">
              cycle {m.data.cycle_id.slice(0, 8)} {m.data.state ? `(${humanize(m.data.state)})` : ""}
            </Link>
          ) : null}
          {m.data.reasons.length ? <span className="text-xs text-muted-foreground">{m.data.reasons.join("; ")}</span> : null}
        </div>
      ) : null}
    </Section>
  );
}
