"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, XOctagon } from "lucide-react";
import { tradingApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { JsonView, KeyValue, PageHeader, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { EnvironmentBadge, GenericStatusBadge, SeverityBadge, SideLabel, SimulatedTag } from "@/components/common/labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { fmtNumber, fmtPrice, fmtSigned, humanize, pnlClass } from "@/lib/utils";
import type { Leg } from "@/types/api";
import { AiAnalysis } from "@/features/integrations/ai-analysis";
import { LIQUIDATE_PHRASE } from "@/features/bots/shutdown-dialog";

const CLOSED_STATES = ["closed", "cancelled", "canceled", "failed", "rejected", "completed"];

function LegCard({ leg, index }: { leg: Leg; index: number }) {
  const t = useTimeFormat();
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Leg {index + 1} <SideLabel side={leg.side} /> <GenericStatusBadge status={leg.status} />
        </span>
      }
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <KeyValue
          items={[
            ["Leg ID", <span key="l" className="text-xs">{leg.id}</span>],
            ["Client order", leg.client_order_id],
            ["Broker order", leg.broker_order_id],
            ["Position", leg.position_id],
            ["Intent status", leg.intent_status],
            ["Volume (req / filled)", `${fmtNumber(leg.requested_volume, 2)} / ${fmtNumber(leg.filled_volume, 2)}`],
            ["Entry", `${fmtPrice(leg.fill_price)} @ ${t.dt(leg.fill_time, true)}`],
            ["Slippage", leg.slippage_pips !== null ? `${fmtNumber(leg.slippage_pips, 1)} pips` : "—"],
            ["Initial stop (intended)", fmtPrice(leg.intended_sl)],
            ["Confirmed stop", fmtPrice(leg.confirmed_sl)],
            ["Take profit", fmtPrice(leg.tp)],
            ["Exit", `${fmtPrice(leg.exit_price)} @ ${t.dt(leg.exit_time, true)}`],
            ["Exit reason", leg.exit_reason ? humanize(leg.exit_reason) : "—"],
            ["Reject reason", leg.reject_reason],
          ]}
        />
        <div className="space-y-3">
          <div>
            <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">P/L breakdown</p>
            <KeyValue
              items={[
                ["Gross", <span key="g" className={pnlClass(leg.gross_pnl)}>{fmtSigned(leg.gross_pnl)}</span>],
                ["Commission", fmtSigned(leg.commission)],
                ["Swap", fmtSigned(leg.swap)],
                ["Net", <span key="n" className={`font-semibold ${pnlClass(leg.net_pnl)}`}>{fmtSigned(leg.net_pnl)}</span>],
                ["Floating", <span key="f" className={pnlClass(leg.floating_pnl)}>{fmtSigned(leg.floating_pnl)}</span>],
              ]}
            />
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Stop modification history</p>
            {leg.stop_history.length === 0 ? (
              <p className="text-xs text-muted-foreground">No stop modifications.</p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Time</TH>
                    <TH>Action</TH>
                    <TH className="text-right">Previous</TH>
                    <TH className="text-right">Stop</TH>
                    <TH>Reason</TH>
                  </TR>
                </THead>
                <TBody>
                  {leg.stop_history.map((s, i) => (
                    <TR key={i}>
                      <TD className="num text-xs">{t.dt(s.time as string | undefined, true)}</TD>
                      <TD className="text-xs">{String(s.action ?? "—").replaceAll("_", " ")}</TD>
                      <TD className="text-right num">{fmtPrice(s.previous as number | null | undefined)}</TD>
                      <TD className="text-right num">{fmtPrice((s.sl ?? s.price) as number | null | undefined)}</TD>
                      <TD className="text-xs">{(s.reason as string | undefined) ?? "—"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </div>
        </div>
      </div>
    </Section>
  );
}

export function CycleDetailView({ cycleId }: { cycleId: string }) {
  const qc = useQueryClient();
  const t = useTimeFormat();
  const { isAdmin } = useSession();
  const [closeOpen, setCloseOpen] = React.useState(false);
  const q = useQuery({ queryKey: qk.cycle(cycleId), queryFn: () => tradingApi.cycle(cycleId), refetchInterval: REFETCH.live });

  if (q.isLoading) return <LoadingBlock rows={10} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data) return null;
  const c = q.data;
  const closed = !!c.closed_at || CLOSED_STATES.includes(c.state.toLowerCase());

  return (
    <div className="space-y-4">
      <Link href="/trades" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Trade history
      </Link>
      <PageHeader
        title={`Cycle ${c.id.slice(0, 8)} · ${c.symbol}`}
        description={`${c.kind === "dual_entry" ? "Dual-entry cycle" : "Single-entry cycle"} · ${c.bot_name} · strategy v${c.strategy_version}`}
        actions={
          <>
            <EnvironmentBadge env={c.environment} />
            <SimulatedTag simulated={c.simulated} />
            <GenericStatusBadge status={c.state} />
            {isAdmin && !closed ? (
              <Button size="sm" variant="destructive" onClick={() => setCloseOpen(true)}>
                <XOctagon />
                Close cycle
              </Button>
            ) : null}
          </>
        }
      />
      {c.error ? <Alert variant="danger">{c.error}</Alert> : null}

      <Section title="Cycle">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <KeyValue
            items={[
              ["Cycle ID", <span key="i" className="text-xs">{c.id}</span>],
              ["Bot", <Link key="b" href={`/bots/${encodeURIComponent(c.bot_id)}`} className="text-primary hover:underline">{c.bot_name}</Link>],
              ["Trigger", humanize(c.trigger_type)],
              ["Event", c.event_id],
              ["Volume", fmtNumber(c.volume, 2)],
              ["Planned risk", fmtNumber(c.planned_risk, 2)],
            ]}
          />
          <KeyValue
            items={[
              ["Realized P/L", <span key="r" className={pnlClass(c.realized_pnl)}>{fmtSigned(c.realized_pnl)}</span>],
              ["Outcome", c.outcome ? <GenericStatusBadge key="o" status={c.outcome} /> : "—"],
              ["Created", t.dt(c.created_at, true)],
              ["Updated", t.dt(c.updated_at, true)],
              ["Closed", t.dt(c.closed_at, true)],
              ["Trailing activated", t.dt(c.trailing_activated_at, true)],
            ]}
          />
        </div>
        {c.notes.length ? (
          <ul className="mt-3 list-inside list-disc text-xs text-muted-foreground">
            {c.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        ) : null}
      </Section>

      <div className="space-y-3 border-l-2 border-primary/40 pl-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Linked legs ({c.legs.length}){c.kind === "dual_entry" ? " — dual entry" : ""}
        </p>
        {c.legs.length === 0 ? <EmptyState title="No legs recorded" /> : c.legs.map((leg, i) => <LegCard key={leg.id} leg={leg} index={i} />)}
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Section title="Risk decision">
          {c.risk_decision ? (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <GenericStatusBadge status={c.risk_decision.approved ? "approved" : "rejected"} />
                <span className="text-xs text-muted-foreground">{t.dt(c.risk_decision.created_at, true)}</span>
                <span className="text-xs">
                  Planned risk {fmtNumber(c.risk_decision.planned_risk, 2)} ({fmtNumber(c.risk_decision.planned_risk_pct, 2)}%)
                </span>
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>Check</TH>
                    <TH>Result</TH>
                    <TH>Detail</TH>
                  </TR>
                </THead>
                <TBody>
                  {c.risk_decision.checks.map((ch) => (
                    <TR key={ch.name}>
                      <TD>{humanize(ch.name)}</TD>
                      <TD>{ch.passed ? <Badge variant="success">Pass</Badge> : <Badge variant="danger">Fail</Badge>}</TD>
                      <TD className="whitespace-normal text-xs">{ch.detail ?? ""}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          ) : (
            <EmptyState title="No risk decision attached" />
          )}
        </Section>
        <Section title="Signal & trailing">
          <p className="mb-1 text-xs text-muted-foreground">Signal</p>
          <JsonView value={c.signal} className="max-h-48" />
          <p className="mb-1 mt-3 text-xs text-muted-foreground">Trailing</p>
          <JsonView value={c.trailing} className="max-h-48" />
        </Section>
        <Section title="Config snapshot">
          <JsonView value={c.config_snapshot} />
        </Section>
        <Section title="Related logs">
          {c.events.length === 0 ? (
            <EmptyState title="No related log entries" />
          ) : (
            <ul className="max-h-96 divide-y divide-border/60 overflow-y-auto">
              {c.events.map((e) => (
                <li key={e.id} className="flex items-start gap-2 py-1.5 text-xs">
                  <SeverityBadge severity={e.severity} />
                  <span className="num text-muted-foreground">{t.dt(e.created_at, true)}</span>
                  <span className="min-w-0 flex-1">{e.message}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <AiAnalysis subject="cycle" id={c.id} />

      <ConfirmDialog
        open={closeOpen}
        onOpenChange={setCloseOpen}
        title={`Close cycle ${c.id.slice(0, 8)}`}
        description={`All open legs of this ${c.symbol} cycle will be closed at market${c.environment === "live" ? " on the LIVE broker account" : ""}.`}
        phrase={LIQUIDATE_PHRASE}
        confirmLabel="Close cycle"
        destructive
        onConfirm={async (confirm) => {
          const res = await tradingApi.closeCycle(c.id, confirm ?? "");
          if (res && typeof res === "object" && "legs" in res) qc.setQueryData(qk.cycle(cycleId), res);
          await qc.invalidateQueries({ queryKey: qk.cycle(cycleId) });
          void qc.invalidateQueries({ queryKey: qk.positions });
        }}
      />
    </div>
  );
}
