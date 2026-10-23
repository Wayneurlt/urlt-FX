"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { marketApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { PageHeader, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { DataSourceBadge, Dot, GenericStatusBadge, OriginBadge } from "@/components/common/labels";
import { LineSeriesChart } from "@/components/common/charts";
import { RecordTable } from "@/components/common/record-table";
import { Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useTimeFormat } from "@/hooks/use-auth";
import { daysAgo } from "@/lib/time";
import { fmtNumber, fmtPrice } from "@/lib/utils";

const TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"];

function QuotesTable() {
  const t = useTimeFormat();
  const q = useQuery({ queryKey: qk.quotes, queryFn: marketApi.quotes, refetchInterval: REFETCH.live });
  if (q.isLoading) return <LoadingBlock rows={5} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data?.length) return <EmptyState title="No quotes available" />;
  const anySynthetic = q.data.some((x) => x.data_source === "synthetic");
  return (
    <div className="space-y-2">
      {anySynthetic ? <Alert variant="warning">SYNTHETIC PRICES — quotes are generated, not real market data.</Alert> : null}
      <Table>
        <THead>
          <TR>
            <TH>Symbol</TH>
            <TH className="text-right">Bid</TH>
            <TH className="text-right">Ask</TH>
            <TH className="text-right">Spread</TH>
            <TH>Time</TH>
            <TH>Fresh</TH>
            <TH>Source</TH>
          </TR>
        </THead>
        <TBody>
          {q.data.map((x) => (
            <TR key={x.symbol}>
              <TD className="font-medium">{x.symbol}</TD>
              <TD className="text-right num">{fmtPrice(x.bid)}</TD>
              <TD className="text-right num">{fmtPrice(x.ask)}</TD>
              <TD className="text-right num">{x.spread_pips === null ? "—" : `${fmtNumber(x.spread_pips, 1)}`}</TD>
              <TD className="num text-xs">{t.time(x.time)}</TD>
              <TD>
                <span className="flex items-center gap-1 text-xs">
                  <Dot ok={x.fresh} /> {x.fresh ? "fresh" : "stale"}
                </span>
              </TD>
              <TD>
                <DataSourceBadge source={x.data_source} />
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function CandleChart({ symbols }: { symbols: string[] }) {
  const t = useTimeFormat();
  const [symbol, setSymbol] = React.useState(symbols[0] ?? "");
  const [tf, setTf] = React.useState("H1");
  React.useEffect(() => {
    if (!symbol && symbols[0]) setSymbol(symbols[0]);
  }, [symbols, symbol]);
  const [start] = React.useState(() => daysAgo(7).toISOString());
  const params = { symbol, timeframe: tf, start };
  const q = useQuery({ queryKey: qk.candles(params), queryFn: () => marketApi.candles(params), enabled: !!symbol });
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="c-symbol">Symbol</Label>
          <Select id="c-symbol" className="w-36" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="c-tf">Timeframe</Label>
          <Select id="c-tf" className="w-24" value={tf} onChange={(e) => setTf(e.target.value)}>
            {TIMEFRAMES.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
        </div>
        {q.data ? <DataSourceBadge source={q.data.data_source} /> : null}
        <span className="text-xs text-muted-foreground">Last 7 days, close price</span>
      </div>
      {!symbol ? (
        <EmptyState title="No instruments" />
      ) : q.isLoading ? (
        <LoadingBlock rows={5} />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : (
        <LineSeriesChart
          data={q.data?.candles ?? []}
          xKey="time"
          series={[{ key: "close", name: `${symbol} close` }]}
          label={`${symbol} ${tf} close`}
          xFormatter={(v) => t.dt(String(v))}
          height={280}
          emptyText="No candles for this period"
        />
      )}
    </div>
  );
}

function NewsTable() {
  const t = useTimeFormat();
  const [start] = React.useState(() => daysAgo(1).toISOString());
  const [end] = React.useState(() => new Date(Date.now() + 7 * 86400_000).toISOString());
  const params = { start, end };
  const events = useQuery({ queryKey: qk.newsEvents(params), queryFn: () => marketApi.newsEvents(params) });
  const provider = useQuery({ queryKey: qk.newsProvider, queryFn: marketApi.newsProvider });
  return (
    <div className="space-y-2">
      {provider.data ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">Provider</span>
          <span className="font-medium">{provider.data.provider}</span>
          <Dot ok={provider.data.available} />
          <span>{provider.data.available ? "available" : "unavailable"}</span>
          <OriginBadge origin={provider.data.origin} />
          <span className="text-muted-foreground">Last fetch {t.dt(provider.data.last_fetch)}</span>
          {provider.data.detail ? <span className="text-muted-foreground">· {provider.data.detail}</span> : null}
        </div>
      ) : provider.error ? (
        <ErrorState error={provider.error} />
      ) : null}
      {provider.data?.origin === "sample" ? (
        <Alert variant="warning">SAMPLE DATA – not real news. Events below are illustrative calendar entries.</Alert>
      ) : null}
      {events.isLoading ? (
        <LoadingBlock rows={5} />
      ) : events.error ? (
        <ErrorState error={events.error} onRetry={() => void events.refetch()} />
      ) : !events.data?.length ? (
        <EmptyState title="No events in the next 7 days" />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Scheduled</TH>
              <TH>Ccy</TH>
              <TH>Event</TH>
              <TH>Impact</TH>
              <TH className="text-right">Forecast</TH>
              <TH className="text-right">Previous</TH>
              <TH className="text-right">Actual</TH>
              <TH>Status</TH>
              <TH>Origin</TH>
            </TR>
          </THead>
          <TBody>
            {events.data.map((e) => (
              <TR key={e.id}>
                <TD className="num text-xs">{t.dt(e.scheduled_at)}</TD>
                <TD>{e.currency}</TD>
                <TD className="max-w-72 truncate" title={e.name}>
                  {e.name}
                </TD>
                <TD>
                  <GenericStatusBadge status={e.impact} />
                </TD>
                <TD className="text-right num">{e.forecast ?? "—"}</TD>
                <TD className="text-right num">{e.previous ?? "—"}</TD>
                <TD className="text-right num">{e.actual ?? "—"}</TD>
                <TD className="text-xs">
                  {e.status ?? "—"} {e.processed ? <Badge variant="muted">processed</Badge> : null}
                </TD>
                <TD>
                  <OriginBadge origin={e.origin} />
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </div>
  );
}

export function MarketPage() {
  const instruments = useQuery({ queryKey: qk.instruments, queryFn: marketApi.instruments });
  const symbols = (instruments.data ?? []).map((i) => i.symbol);
  return (
    <div className="space-y-3">
      <PageHeader title="Market & News" description="Quotes, instrument specifications and the economic calendar, labelled by data source." />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Section title="Quotes">
          <QuotesTable />
        </Section>
        <Section title="Price chart">
          {instruments.isLoading ? <LoadingBlock rows={5} /> : instruments.error ? <ErrorState error={instruments.error} /> : <CandleChart symbols={symbols} />}
        </Section>
      </div>
      <Section title="Economic calendar">
        <NewsTable />
      </Section>
      <Section title="Instrument specifications">
        {instruments.isLoading ? (
          <LoadingBlock rows={4} />
        ) : instruments.error ? (
          <ErrorState error={instruments.error} />
        ) : (
          <RecordTable rows={(instruments.data ?? []) as unknown as Record<string, unknown>[]} emptyText="No instruments" maxColumns={16} />
        )}
      </Section>
    </div>
  );
}
