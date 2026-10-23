"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { backtestApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/client";
import { qk, REFETCH } from "@/lib/query-keys";
import { JsonView, PageHeader, Pagination, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { DataSourceBadge, GenericStatusBadge } from "@/components/common/labels";
import { BotChip } from "@/components/common/bot-chip";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { fmtNumber, fmtPct, fmtSigned, pnlClass } from "@/lib/utils";
import type { BacktestRun, BotKind } from "@/types/api";
import { BacktestForm } from "./backtest-form";

export function ProgressBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value || 0)) * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Progress">
        <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-xs text-muted-foreground">{pct}%</span>
    </div>
  );
}

function isActiveRun(r: BacktestRun) {
  return r.status === "queued" || r.status === "running";
}

export function RunsList({ botKind }: { botKind?: BotKind }) {
  const [page, setPage] = React.useState(1);
  const t = useTimeFormat();
  const runs = useQuery({
    queryKey: qk.backtests(page, botKind),
    queryFn: () => backtestApi.list(page, 20, botKind),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.items.some(isActiveRun) ? REFETCH.backtestRunning : false),
  });
  if (runs.isLoading) return <LoadingBlock rows={5} />;
  if (runs.error) return <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />;
  if (!runs.data || runs.data.items.length === 0) return <EmptyState title="No backtests yet" description="Configure and queue one above." />;
  return (
    <>
      <Table>
        <THead>
          <TR>
            <TH>Run</TH>
            <TH>Created</TH>
            {botKind ? null : <TH>Bot</TH>}
            <TH>Symbol</TH>
            <TH>Period</TH>
            <TH>Data</TH>
            <TH>Status</TH>
            <TH className="text-right">Net P/L</TH>
            <TH className="text-right">Return</TH>
            <TH className="text-right">Max DD</TH>
            <TH className="text-right">Trades</TH>
          </TR>
        </THead>
        <TBody>
          {runs.data.items.map((r) => (
            <TR key={r.id}>
              <TD>
                <Link href={`/backtests/${encodeURIComponent(r.id)}`} className="num text-xs text-primary hover:underline">
                  {r.id.slice(0, 8)}
                </Link>
              </TD>
              <TD className="num text-xs">{t.dt(r.created_at)}</TD>
              {botKind ? null : (
                <TD>
                  <BotChip bot={r.bot_kind} />
                </TD>
              )}
              <TD>{r.symbol}</TD>
              <TD className="num text-xs">
                {r.start.slice(0, 10)} → {r.end.slice(0, 10)}
              </TD>
              <TD>
                <DataSourceBadge source={r.data_source} />
              </TD>
              <TD>
                <div className="flex items-center gap-2">
                  <GenericStatusBadge status={r.status} />
                  {isActiveRun(r) ? <ProgressBar value={r.progress} /> : null}
                </div>
                {r.error ? <p className="max-w-56 truncate text-xs text-loss" title={r.error}>{r.error}</p> : null}
              </TD>
              <TD className={`text-right num ${pnlClass(r.summary?.net_pnl)}`}>{fmtSigned(r.summary?.net_pnl)}</TD>
              <TD className="text-right num">{fmtPct(r.summary?.return_pct)}</TD>
              <TD className="text-right num">{fmtPct(r.summary?.max_drawdown_pct)}</TD>
              <TD className="text-right num">{fmtNumber(r.summary?.trade_count, 0)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <Pagination page={runs.data.page} pageSize={runs.data.page_size} total={runs.data.total} onPageChange={setPage} />
    </>
  );
}

function DatasetsPanel() {
  const qc = useQueryClient();
  const t = useTimeFormat();
  const { isAdmin } = useSession();
  const datasets = useQuery({ queryKey: qk.datasets, queryFn: backtestApi.datasets });
  const [file, setFile] = React.useState<File | null>(null);
  const [symbol, setSymbol] = React.useState("");
  const [timeframe, setTimeframe] = React.useState("M1");
  const [sourceTz, setSourceTz] = React.useState("UTC");
  const [formError, setFormError] = React.useState<string | null>(null);
  const imp = useMutation({
    mutationFn: (fd: FormData) => backtestApi.importDataset(fd),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.datasets }),
  });

  return (
    <div className="space-y-3">
      {isAdmin ? (
        <form
          className="grid grid-cols-2 gap-2 md:grid-cols-5"
          aria-label="Import dataset"
          onSubmit={(e) => {
            e.preventDefault();
            setFormError(null);
            if (!file) return setFormError("Choose a CSV file");
            if (!/^[A-Za-z0-9._]{3,20}$/.test(symbol.trim())) return setFormError("Enter a valid symbol");
            const fd = new FormData();
            fd.append("file", file);
            fd.append("symbol", symbol.trim().toUpperCase());
            fd.append("timeframe", timeframe);
            fd.append("source_tz", sourceTz.trim() || "UTC");
            imp.mutate(fd);
          }}
        >
          <div className="col-span-2 space-y-1">
            <Label htmlFor="ds-file">CSV file</Label>
            <Input id="ds-file" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ds-symbol">Symbol</Label>
            <Input id="ds-symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="EURUSD" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ds-tf">Timeframe</Label>
            <Input id="ds-tf" value={timeframe} onChange={(e) => setTimeframe(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ds-tz">Source timezone</Label>
            <Input id="ds-tz" value={sourceTz} onChange={(e) => setSourceTz(e.target.value)} />
          </div>
          <div className="col-span-full flex items-center justify-end gap-2">
            {formError ? <span className="text-xs text-loss" role="alert">{formError}</span> : null}
            <Button type="submit" size="sm" disabled={imp.isPending}>
              {imp.isPending ? "Importing…" : "Import CSV"}
            </Button>
          </div>
        </form>
      ) : null}
      {imp.error ? <Alert variant="danger">{errorMessage(imp.error)}</Alert> : null}
      {imp.data ? (
        <div>
          <Alert variant="success">Dataset imported. Quality report:</Alert>
          <JsonView value={imp.data.quality ?? imp.data} className="mt-2 max-h-56" />
        </div>
      ) : null}
      {datasets.isLoading ? (
        <LoadingBlock rows={3} />
      ) : datasets.error ? (
        <ErrorState error={datasets.error} />
      ) : !datasets.data?.length ? (
        <EmptyState title="No datasets imported" description="Backtests without a dataset use the SYNTHETIC generator." />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Symbol</TH>
              <TH>TF</TH>
              <TH className="text-right">Rows</TH>
              <TH>Range</TH>
              <TH>Source</TH>
              <TH>Quality</TH>
              <TH>Imported</TH>
            </TR>
          </THead>
          <TBody>
            {datasets.data.map((d) => (
              <TR key={d.id}>
                <TD>{d.symbol}</TD>
                <TD>{d.timeframe}</TD>
                <TD className="text-right num">{d.rows.toLocaleString()}</TD>
                <TD className="num text-xs">
                  {t.date(d.start)} → {t.date(d.end)}
                </TD>
                <TD>{d.source}</TD>
                <TD className="max-w-60 truncate text-xs" title={JSON.stringify(d.quality)}>
                  {typeof d.quality === "string" ? d.quality : JSON.stringify(d.quality)}
                </TD>
                <TD className="num text-xs">{t.dt(d.created_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </div>
  );
}

function BacktestsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const { isAdmin } = useSession();
  const kind = params.get("bot_kind");
  const initialKind: BotKind | undefined = kind === "news_volatility" || kind === "daily_trading" ? kind : undefined;
  return (
    <div className="space-y-3">
      <PageHeader title="Backtesting" description="Historical or synthetic simulations. Results are research output, not live performance." />
      {isAdmin ? (
        <Section title="New backtest">
          <BacktestForm initialBotKind={initialKind} onCreated={(run) => router.push(`/backtests/${encodeURIComponent(run.id)}`)} />
        </Section>
      ) : null}
      <Section title="Runs">
        <RunsList />
      </Section>
      <Section title="Datasets">
        <DatasetsPanel />
      </Section>
    </div>
  );
}

export function BacktestsPage() {
  return (
    <React.Suspense fallback={<LoadingBlock rows={6} />}>
      <BacktestsInner />
    </React.Suspense>
  );
}
