"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { tradingApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { PageHeader, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { EnvironmentBadge, SideLabel, SimulatedTag } from "@/components/common/labels";
import { Badge } from "@/components/ui/badge";
import { BotChip } from "@/components/common/bot-chip";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useTimeFormat } from "@/hooks/use-auth";
import { fmtNumber, fmtPrice, fmtSigned, pnlClass } from "@/lib/utils";
import type { Position } from "@/types/api";

export function PositionsTable({ positions, hideBot = false }: { positions: Position[]; hideBot?: boolean }) {
  const t = useTimeFormat();
  if (positions.length === 0) return <EmptyState title="No open positions" />;
  return (
    <Table>
      <THead>
        <TR>
          <TH>Cycle</TH>
          {hideBot ? null : <TH>Bot</TH>}
          <TH>Symbol</TH>
          <TH>Side</TH>
          <TH className="text-right">Volume</TH>
          <TH className="text-right">Entry</TH>
          <TH className="text-right">Current</TH>
          <TH className="text-right">SL</TH>
          <TH className="text-right">TP</TH>
          <TH className="text-right">Floating P/L</TH>
          <TH>Opened</TH>
          <TH>Flags</TH>
        </TR>
      </THead>
      <TBody>
        {positions.map((p) => (
          <TR key={p.leg_id}>
            <TD>
              <Link href={`/cycles/${encodeURIComponent(p.cycle_id)}`} className="num text-xs text-primary hover:underline">
                {p.cycle_id.slice(0, 8)}
              </Link>
            </TD>
            {hideBot ? null : (
              <TD>
                <BotChip bot={p.bot_id} />
              </TD>
            )}
            <TD className="font-medium">{p.symbol}</TD>
            <TD>
              <SideLabel side={p.side} />
            </TD>
            <TD className="text-right num">{fmtNumber(p.volume, 2)}</TD>
            <TD className="text-right num">{fmtPrice(p.entry_price)}</TD>
            <TD className="text-right num">{fmtPrice(p.current_price)}</TD>
            <TD className="text-right num">{fmtPrice(p.stop_loss)}</TD>
            <TD className="text-right num">{fmtPrice(p.take_profit)}</TD>
            <TD className={`text-right num ${pnlClass(p.floating_pnl)}`}>{fmtSigned(p.floating_pnl)}</TD>
            <TD className="num text-xs">{t.dt(p.opened_at)}</TD>
            <TD>
              <span className="flex gap-1">
                <SimulatedTag simulated={p.simulated} />
                <EnvironmentBadge env={p.environment} />
                {p.trailing_active ? <Badge variant="info">Trailing</Badge> : null}
              </span>
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

export function PositionsPage() {
  const q = useQuery({ queryKey: qk.positions, queryFn: tradingApi.positions, refetchInterval: REFETCH.live });
  const total = (q.data ?? []).reduce((s, p) => s + (p.floating_pnl ?? 0), 0);
  return (
    <div>
      <PageHeader
        title="Open Positions"
        description="Open legs from both bots, refreshed every 5 seconds. Open a bot's workspace to see only its positions."
        actions={
          q.data && q.data.length ? (
            <span className="text-sm">
              Floating total (as reported): <span className={`num ${pnlClass(total)}`}>{fmtSigned(total)}</span>
            </span>
          ) : null
        }
      />
      <Section title={`Positions${q.data ? ` (${q.data.length})` : ""}`}>
        {q.isLoading ? <LoadingBlock rows={6} /> : q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : <PositionsTable positions={q.data ?? []} />}
      </Section>
    </div>
  );
}
