"use client";
import * as React from "react";
import Link from "next/link";
import { EmptyState } from "@/components/common/states";
import { GenericStatusBadge, SimulatedTag } from "@/components/common/labels";
import { Badge } from "@/components/ui/badge";
import { BotChip } from "@/components/common/bot-chip";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useTimeFormat } from "@/hooks/use-auth";
import { fmtNumber, fmtSigned, humanize, pnlClass } from "@/lib/utils";
import type { Cycle } from "@/types/api";

export function CyclesTable({ cycles, showBot = false }: { cycles: Cycle[]; showBot?: boolean }) {
  const t = useTimeFormat();
  if (cycles.length === 0) return <EmptyState title="No cycles" />;
  return (
    <Table>
      <THead>
        <TR>
          <TH>Cycle</TH>
          {showBot ? <TH>Bot</TH> : null}
          <TH>Kind</TH>
          <TH>Symbol</TH>
          <TH>Trigger</TH>
          <TH>State</TH>
          <TH className="text-right">Volume</TH>
          <TH className="text-right">Planned risk</TH>
          <TH className="text-right">Realized</TH>
          <TH>Outcome</TH>
          <TH>Created</TH>
          <TH />
        </TR>
      </THead>
      <TBody>
        {cycles.map((c) => (
          <TR key={c.id}>
            <TD>
              <Link href={`/cycles/${encodeURIComponent(c.id)}`} className="num text-xs text-primary hover:underline">
                {c.id.slice(0, 8)}
              </Link>
            </TD>
            {showBot ? (
              <TD>
                <BotChip bot={c.bot_id} />
              </TD>
            ) : null}
            <TD>{c.kind === "dual_entry" ? <Badge variant="info">Dual entry</Badge> : <Badge variant="muted">Single</Badge>}</TD>
            <TD>{c.symbol}</TD>
            <TD className="text-xs">{humanize(c.trigger_type)}</TD>
            <TD>
              <GenericStatusBadge status={c.state} />
            </TD>
            <TD className="text-right num">{fmtNumber(c.volume, 2)}</TD>
            <TD className="text-right num">{fmtNumber(c.planned_risk, 2)}</TD>
            <TD className={`text-right num ${pnlClass(c.realized_pnl)}`}>{fmtSigned(c.realized_pnl)}</TD>
            <TD>{c.outcome ? <GenericStatusBadge status={c.outcome} /> : "—"}</TD>
            <TD className="num text-xs">{t.dt(c.created_at)}</TD>
            <TD>
              <SimulatedTag simulated={c.simulated} />
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
