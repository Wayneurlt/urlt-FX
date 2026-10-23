"use client";
import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Download } from "lucide-react";
import { backtestApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/client";
import { qk, REFETCH } from "@/lib/query-keys";
import { JsonView, KeyValue, PageHeader, Pagination, Section, StatCard } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { DataSourceBadge, GenericStatusBadge } from "@/components/common/labels";
import { DrawdownChart, LineSeriesChart, SignedBarChart } from "@/components/common/charts";
import { RecordTable, formatCell, toRows } from "@/components/common/record-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Select, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { humanize, isNum } from "@/lib/utils";
import { VALIDATION_KINDS, type BacktestDetail, type ValidationKind } from "@/types/api";
import { AiAnalysis } from "@/features/integrations/ai-analysis";
import { ProgressBar } from "./backtests-page";
import { parseJsonObject } from "@/features/bots/bot-config-tab";

/** Peak-to-trough drawdown (%) of the backend's equity curve — presentation only. */
export function drawdownSeries(curve: { time: string; equity: number }[]) {
  let peak = -Infinity;
  return curve.map((p) => {
    peak = Math.max(peak, p.equity);
    return { time: p.time, drawdown_pct: peak > 0 ? ((peak - p.equity) / peak) * 100 : 0 };
  });
}

function scalarEntries(obj: Record<string, unknown> | null | undefined) {
  if (!obj) return [] as [string, unknown][];
  return Object.entries(obj).filter(([, v]) => v === null || typeof v !== "object");
}

function Ledger({ id, kind }: { id: string; kind: "trades" | "cycles" }) {
  const [page, setPage] = React.useState(1);
  const q = useQuery({
    queryKey: kind === "trades" ? qk.backtestTrades(id, page) : qk.backtestCycles(id, page),
    queryFn: () => (kind === "trades" ? backtestApi.trades(id, page) : backtestApi.cycles(id, page)),
    placeholderData: keepPreviousData,
  });
  if (q.isLoading) return <LoadingBlock rows={6} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data) return null;
  return (
    <>
      <RecordTable rows={q.data.items} emptyText={`No ${kind} in this run`} />
      <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPageChange={setPage} />
    </>
  );
}

function ValidationPanel({ detail }: { detail: BacktestDetail }) {
  const qc = useQueryClient();
  const { isAdmin } = useSession();
  const [kind, setKind] = React.useState<ValidationKind>("in_out_sample");
  const [params, setParams] = React.useState("{}");
  const parsed = parseJsonObject(params);
  const m = useMutation({
    mutationFn: () => backtestApi.validate(detail.id, { kind, params: parsed.value ?? {} }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.backtest(detail.id) }),
  });
  const results = toRows(detail.validation ?? {}, "kind");
  return (
    <div className="space-y-3">
      {isAdmin ? (
        <form
          className="grid grid-cols-1 gap-2 md:grid-cols-[200px_1fr_auto] md:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            if (!parsed.error) m.mutate();
          }}
          aria-label="Queue validation"
        >
          <div className="space-y-1">
            <Label htmlFor="val-kind">Validation kind</Label>
            <Select id="val-kind" value={kind} onChange={(e) => setKind(e.target.value as ValidationKind)}>
              {VALIDATION_KINDS.map((k) => (
                <option key={k} value={k}>
                  {humanize(k)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="val-params">Params (JSON)</Label>
            <Textarea id="val-params" className="min-h-9" rows={2} value={params} onChange={(e) => setParams(e.target.value)} aria-invalid={!!parsed.error} />
            {parsed.error ? <p className="text-xs text-loss">{parsed.error}</p> : null}
          </div>
          <Button type="submit" size="sm" disabled={m.isPending || !!parsed.error || detail.status !== "succeeded"}>
            {m.isPending ? "Queuing…" : "Queue validation"}
          </Button>
        </form>
      ) : null}
      {m.error ? <Alert variant="danger">{errorMessage(m.error)}</Alert> : null}
      {m.isSuccess ? <Alert variant="success">Validation queued. Results appear below when the job completes.</Alert> : null}
      {results.length === 0 ? (
        <EmptyState title="No validation results yet" />
      ) : (
        <div className="space-y-2">
          {Object.entries(detail.validation ?? {}).map(([k, v]) => (
            <details key={k} className="rounded-md border border-border p-2" open>
              <summary className="cursor-pointer text-sm font-medium">{humanize(k)}</summary>
              <JsonView value={v} className="mt-2" />
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

function Breakdown({ title, data, keyName }: { title: string; data: unknown; keyName: string }) {
  const rows = toRows(data, keyName);
  const numericKey = rows.length
    ? ["net_pnl", "pnl", "return_pct", "profit"].find((k) => isNum(rows[0][k])) ?? Object.keys(rows[0]).find((k) => k !== keyName && isNum(rows[0][k]))
    : undefined;
  return (
    <Section title={title}>
      {rows.length === 0 ? (
        <EmptyState title="No data" />
      ) : (
        <div className="space-y-3">
          {numericKey ? (
            <SignedBarChart data={rows} xKey={keyName} yKey={numericKey} label={`${title} — ${humanize(numericKey)}`} height={200} />
          ) : null}
          <RecordTable rows={rows} />
        </div>
      )}
    </Section>
  );
}

export function BacktestDetailView({ backtestId }: { backtestId: string }) {
  const t = useTimeFormat();
  const q = useQuery({
    queryKey: qk.backtest(backtestId),
    queryFn: () => backtestApi.get(backtestId),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "queued" || s === "running" ? REFETCH.backtestRunning : false;
    },
  });

  if (q.isLoading) return <LoadingBlock rows={10} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data) return null;
  const d = q.data;
  const metrics = (d.metrics ?? {}) as Record<string, unknown>;
  const running = d.status === "queued" || d.status === "running";
  const synthetic = d.data_source === "synthetic";

  return (
    <div className="space-y-4">
      <Link href="/backtests" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> All backtests
      </Link>
      <PageHeader
        title={`Backtest ${d.id.slice(0, 8)} · ${d.symbol}`}
        description={`${humanize(d.bot_kind)} · ${d.start.slice(0, 10)} → ${d.end.slice(0, 10)} · created ${t.dt(d.created_at)}`}
        actions={
          <>
            <DataSourceBadge source={d.data_source} />
            <GenericStatusBadge status={d.status} />
            {running ? <ProgressBar value={d.progress} /> : null}
            {d.status === "succeeded" ? (
              <Button asChild size="sm" variant="outline">
                <a href={backtestApi.exportUrl(d.id)} download>
                  <Download />
                  Export CSV
                </a>
              </Button>
            ) : null}
          </>
        }
      />
      {synthetic ? (
        <Alert variant="warning">
          This run used SYNTHETIC generated prices. Results say nothing about real market behaviour.
        </Alert>
      ) : null}
      {d.error ? <Alert variant="danger">Run failed: {d.error}</Alert> : null}
      {d.warnings.length ? (
        <div role="alert" className="rounded-md border border-warning/60 bg-warning/10 p-3">
          <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-warning">
            <AlertTriangle className="size-4" aria-hidden="true" /> Warnings ({d.warnings.length})
          </p>
          <ul className="list-inside list-disc space-y-0.5 text-sm text-warning">
            {d.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {d.summary ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
          {scalarEntries(d.summary).map(([k, v]) => (
            <StatCard
              key={k}
              label={humanize(k)}
              value={v === null ? "Not enough data" : formatCell(v)}
              valueClassName={
                v === null
                  ? "text-sm font-normal text-muted-foreground"
                  : isNum(v) && /pnl|return/.test(k)
                    ? v > 0
                      ? "text-profit"
                      : v < 0
                        ? "text-loss"
                        : ""
                    : ""
              }
            />
          ))}
        </div>
      ) : running ? (
        <EmptyState title="Run in progress" description="Summary appears when the run completes." />
      ) : null}

      <Tabs defaultValue="charts">
        <TabsList>
          <TabsTrigger value="charts">Charts & metrics</TabsTrigger>
          <TabsTrigger value="trades">Trade ledger</TabsTrigger>
          <TabsTrigger value="cycles">Cycle ledger</TabsTrigger>
          <TabsTrigger value="validation">Validation</TabsTrigger>
          <TabsTrigger value="repro">Parameters & reproducibility</TabsTrigger>
        </TabsList>
        <TabsContent value="charts" className="space-y-3">
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Section title="Equity curve">
              <LineSeriesChart
                data={d.equity_curve}
                xKey="time"
                series={[{ key: "equity", name: "Equity" }]}
                label="Backtest equity"
                xFormatter={(v) => t.date(String(v))}
                emptyText="No equity data"
              />
            </Section>
            <Section title="Drawdown (from equity curve)">
              <DrawdownChart data={drawdownSeries(d.equity_curve)} xKey="time" yKey="drawdown_pct" label="Backtest drawdown" xFormatter={(v) => t.date(String(v))} />
            </Section>
            <Breakdown title="Monthly returns" data={metrics["by_month"]} keyName="month" />
            <Breakdown title="By instrument" data={metrics["by_instrument"] ?? metrics["by_symbol"]} keyName="symbol" />
            <Breakdown title="By trigger" data={metrics["by_trigger"]} keyName="trigger" />
            <Section title="Metrics">
              <KeyValue items={scalarEntries(metrics).map(([k, v]) => [humanize(k), formatCell(v)])} />
              {scalarEntries(metrics).length === 0 ? <EmptyState title="No metrics" /> : null}
            </Section>
          </div>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Section title="Assumptions">
              {d.assumptions.length ? (
                <ul className="list-inside list-disc space-y-0.5 text-sm">
                  {d.assumptions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No assumptions listed" />
              )}
            </Section>
            <Section title="Counters">
              <KeyValue items={scalarEntries(d.counters).map(([k, v]) => [humanize(k), formatCell(v)])} />
              {scalarEntries(d.counters).length === 0 ? <EmptyState title="No counters" /> : null}
            </Section>
          </div>
          <AiAnalysis subject="backtest" id={d.id} />
        </TabsContent>
        <TabsContent value="trades">
          <Section title="Trades">
            <Ledger id={d.id} kind="trades" />
          </Section>
        </TabsContent>
        <TabsContent value="cycles">
          <Section title="Cycles">
            <Ledger id={d.id} kind="cycles" />
          </Section>
        </TabsContent>
        <TabsContent value="validation">
          <Section title="Validation">
            <ValidationPanel detail={d} />
          </Section>
        </TabsContent>
        <TabsContent value="repro" className="space-y-3">
          <Section title="Reproducibility">
            <KeyValue items={[["Data fingerprint", <span key="f" className="break-all text-xs">{d.data_fingerprint ?? "—"}</span>], ["Data source", d.data_source]]} />
            <JsonView value={d.reproducibility} className="mt-2" />
          </Section>
          <Section title="Parameter snapshot">
            <JsonView value={d.config} />
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
