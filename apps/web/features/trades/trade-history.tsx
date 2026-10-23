"use client";
import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { botsApi, tradingApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/query-keys";
import { PageHeader, Pagination, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { EnvironmentBadge, GenericStatusBadge, SideLabel, SimulatedTag } from "@/components/common/labels";
import { BotChip } from "@/components/common/bot-chip";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useTimeFormat } from "@/hooks/use-auth";
import { dayEndIso, dayStartIso } from "@/lib/time";
import { fmtNumber, fmtPrice, fmtSigned, humanize, pnlClass } from "@/lib/utils";
import type { TradeFilters } from "@/types/api";

export interface TradeFilterForm {
  start: string; // YYYY-MM-DD
  end: string;
  bot_id: string;
  strategy_version: string;
  symbol: string;
  side: string;
  environment: string;
  trigger: string;
  outcome: string;
  status: string;
  cycle_id: string;
  sort: string;
}

export const EMPTY_TRADE_FILTERS: TradeFilterForm = {
  start: "",
  end: "",
  bot_id: "",
  strategy_version: "",
  symbol: "",
  side: "",
  environment: "",
  trigger: "",
  outcome: "",
  status: "",
  cycle_id: "",
  sort: "-fill_time",
};

/** Converts form state into API filters (dates → UTC ISO bounds, trimmed strings, empties dropped by buildQuery). */
export function toTradeFilters(form: TradeFilterForm, page: number, pageSize: number): TradeFilters {
  return {
    start: dayStartIso(form.start) || undefined,
    end: dayEndIso(form.end) || undefined,
    bot_id: form.bot_id || undefined,
    strategy_version: form.strategy_version.trim() || undefined,
    symbol: form.symbol.trim().toUpperCase() || undefined,
    side: form.side || undefined,
    environment: form.environment || undefined,
    trigger: form.trigger.trim() || undefined,
    outcome: (form.outcome || undefined) as TradeFilters["outcome"],
    status: form.status.trim() || undefined,
    cycle_id: form.cycle_id.trim() || undefined,
    sort: form.sort || undefined,
    page,
    page_size: pageSize,
  };
}

const PAGE_SIZE = 50;

function FilterField({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

/** Trade ledger. With `botId` it is scoped to one bot (bot workspace) and the bot filter is hidden. */
export function TradeHistory({ botId }: { botId?: string } = {}) {
  const t = useTimeFormat();
  const initial = React.useMemo(() => ({ ...EMPTY_TRADE_FILTERS, bot_id: botId ?? "" }), [botId]);
  const [form, setForm] = React.useState<TradeFilterForm>(initial);
  const [applied, setApplied] = React.useState<TradeFilterForm>(initial);
  const [page, setPage] = React.useState(1);
  const bots = useQuery({ queryKey: qk.bots, queryFn: botsApi.list });

  const filters = toTradeFilters(applied, page, PAGE_SIZE);
  const trades = useQuery({
    queryKey: qk.trades(filters),
    queryFn: () => tradingApi.trades(filters),
    placeholderData: keepPreviousData,
  });

  const set = (k: keyof TradeFilterForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="space-y-3">
      <PageHeader
        title={botId ? "Trades" : "Trade History"}
        description={
          botId
            ? "Every leg this bot has traded. Simulated fills are tagged and are not broker executions."
            : "Every leg of every cycle, from both bots. Simulated fills are tagged and are not broker executions."
        }
        actions={
          <Button asChild size="sm" variant="outline">
            <a href={tradingApi.tradesExportUrl(filters)} download>
              <Download />
              Export CSV
            </a>
          </Button>
        }
      />
      <Section title="Filters">
        <form
          className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6"
          onSubmit={(e) => {
            e.preventDefault();
            setApplied(form);
            setPage(1);
          }}
          aria-label="Trade filters"
        >
          <FilterField id="f-start" label="From">
            <Input id="f-start" type="date" value={form.start} onChange={set("start")} />
          </FilterField>
          <FilterField id="f-end" label="To">
            <Input id="f-end" type="date" value={form.end} onChange={set("end")} />
          </FilterField>
          {botId ? null : (
            <FilterField id="f-bot" label="Bot">
              <Select id="f-bot" value={form.bot_id} onChange={set("bot_id")}>
                <option value="">All bots</option>
                {bots.data?.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </Select>
            </FilterField>
          )}
          <FilterField id="f-ver" label="Strategy version">
            <Input id="f-ver" value={form.strategy_version} onChange={set("strategy_version")} />
          </FilterField>
          <FilterField id="f-symbol" label="Instrument">
            <Input id="f-symbol" placeholder="EURUSD" value={form.symbol} onChange={set("symbol")} />
          </FilterField>
          <FilterField id="f-side" label="Direction">
            <Select id="f-side" value={form.side} onChange={set("side")}>
              <option value="">Any</option>
              <option value="BUY">Buy</option>
              <option value="SELL">Sell</option>
            </Select>
          </FilterField>
          <FilterField id="f-env" label="Environment">
            <Select id="f-env" value={form.environment} onChange={set("environment")}>
              <option value="">Any</option>
              <option value="simulation">Simulation</option>
              <option value="paper">Paper</option>
              <option value="live">Live</option>
            </Select>
          </FilterField>
          <FilterField id="f-trigger" label="Trigger">
            <Input id="f-trigger" placeholder="news / daily…" value={form.trigger} onChange={set("trigger")} />
          </FilterField>
          <FilterField id="f-outcome" label="Outcome">
            <Select id="f-outcome" value={form.outcome} onChange={set("outcome")}>
              <option value="">Any</option>
              <option value="win">Win</option>
              <option value="loss">Loss</option>
              <option value="open">Open</option>
            </Select>
          </FilterField>
          <FilterField id="f-status" label="Status">
            <Input id="f-status" placeholder="filled / closed…" value={form.status} onChange={set("status")} />
          </FilterField>
          <FilterField id="f-cycle" label="Cycle ID">
            <Input id="f-cycle" value={form.cycle_id} onChange={set("cycle_id")} />
          </FilterField>
          <FilterField id="f-sort" label="Sort">
            <Select id="f-sort" value={form.sort} onChange={set("sort")}>
              <option value="-fill_time">Newest first</option>
              <option value="fill_time">Oldest first</option>
              <option value="-net_pnl">Best P/L</option>
              <option value="net_pnl">Worst P/L</option>
            </Select>
          </FilterField>
          <div className="col-span-full flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setForm(initial);
                setApplied(initial);
                setPage(1);
              }}
            >
              Reset
            </Button>
            <Button type="submit" size="sm">
              Apply filters
            </Button>
          </div>
        </form>
      </Section>

      <Section title={`Trades${trades.data ? ` (${trades.data.total})` : ""}`}>
        {trades.isLoading ? (
          <LoadingBlock rows={8} />
        ) : trades.error ? (
          <ErrorState error={trades.error} onRetry={() => void trades.refetch()} />
        ) : !trades.data || trades.data.items.length === 0 ? (
          <EmptyState title="No trades match these filters" />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH>Fill time</TH>
                  <TH>Cycle</TH>
                  {botId ? null : <TH>Bot</TH>}
                  <TH>Ver.</TH>
                  <TH>Symbol</TH>
                  <TH>Side</TH>
                  <TH>Trigger</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Vol</TH>
                  <TH className="text-right">Fill</TH>
                  <TH className="text-right">Exit</TH>
                  <TH>Exit reason</TH>
                  <TH className="text-right">Net P/L</TH>
                  <TH>Env</TH>
                </TR>
              </THead>
              <TBody>
                {trades.data.items.map((r) => (
                  <TR key={r.id}>
                    <TD className="num text-xs">{t.dt(r.fill_time)}</TD>
                    <TD>
                      <Link href={`/cycles/${encodeURIComponent(r.cycle_id)}`} className="num text-xs text-primary hover:underline">
                        {r.cycle_id.slice(0, 8)}
                      </Link>
                    </TD>
                    {botId ? null : (
                      <TD>
                        <BotChip bot={r.bot_id} />
                      </TD>
                    )}
                    <TD className="num text-xs">{r.strategy_version}</TD>
                    <TD>{r.symbol}</TD>
                    <TD>
                      <SideLabel side={r.side} />
                    </TD>
                    <TD className="text-xs">{humanize(r.trigger_type)}</TD>
                    <TD>
                      <GenericStatusBadge status={r.status} />
                    </TD>
                    <TD className="text-right num">{fmtNumber(r.filled_volume ?? r.requested_volume, 2)}</TD>
                    <TD className="text-right num">{fmtPrice(r.fill_price)}</TD>
                    <TD className="text-right num">{fmtPrice(r.exit_price)}</TD>
                    <TD className="text-xs">{r.exit_reason ? humanize(r.exit_reason) : r.reject_reason ?? "—"}</TD>
                    <TD className={`text-right num ${pnlClass(r.net_pnl)}`}>{fmtSigned(r.net_pnl)}</TD>
                    <TD>
                      <span className="flex gap-1">
                        <EnvironmentBadge env={r.environment} />
                        <SimulatedTag simulated={r.simulated} />
                      </span>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <Pagination page={trades.data.page} pageSize={trades.data.page_size} total={trades.data.total} onPageChange={setPage} />
          </>
        )}
      </Section>
    </div>
  );
}
