"use client";
import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { riskApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";
import { Meter, PageHeader, Pagination, Section, StatCard } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { GenericStatusBadge } from "@/components/common/labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { fmtMoney, fmtNumber, fmtPct, humanize } from "@/lib/utils";
import type { Breaker, RiskStatus } from "@/types/api";
import { DiffTable, RiskSettingsForm } from "./risk-settings-form";
import { AiAnalysis } from "@/features/integrations/ai-analysis";

export const CLEAR_PHRASE = "CLEAR";

function StatusPanel({ s }: { s: RiskStatus }) {
  return (
    <div className="space-y-3">
      {s.halted ? (
        <Alert variant="danger">
          <strong>Trading halted.</strong> {s.halt_reasons.join("; ")}
        </Alert>
      ) : null}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Section title="Daily loss">
          <Meter
            label="Used vs limit"
            value={s.daily_loss}
            max={s.daily_loss_limit}
            display={`${fmtMoney(s.daily_loss, s.currency)} / ${fmtMoney(s.daily_loss_limit, s.currency)} (${fmtPct(s.daily_loss_used_pct, 1)})`}
          />
        </Section>
        <Section title="Drawdown">
          <Meter label="Drawdown vs limit" value={s.drawdown_pct} max={s.drawdown_limit_pct} display={`${fmtPct(s.drawdown_pct)} / ${fmtPct(s.drawdown_limit_pct)}`} />
        </Section>
        <Section title="Open risk">
          <Meter label="Open risk vs cap" value={s.open_risk} max={s.open_risk_cap} display={`${fmtMoney(s.open_risk, s.currency)} / ${fmtMoney(s.open_risk_cap, s.currency)}`} />
        </Section>
        <Section title="Concurrent cycles">
          <Meter label="Active vs max" value={s.active_cycles} max={s.max_concurrent_cycles} display={`${s.active_cycles} / ${s.max_concurrent_cycles ?? "—"}`} />
        </Section>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <StatCard label="Equity" value={fmtMoney(s.equity, s.currency)} />
        <StatCard label="Day-start equity" value={fmtMoney(s.day_start_equity, s.currency)} />
        <StatCard label="Blocked attempts today" value={fmtNumber(s.blocked_attempts_today, 0)} />
        <StatCard label="Status" value={s.halted ? "Halted" : "Trading allowed"} valueClassName={s.halted ? "text-loss" : "text-profit"} />
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Section title="Currency exposure">
          {s.currency_exposure.length === 0 ? (
            <EmptyState title="No currency exposure" />
          ) : (
            <div className="space-y-3">
              {s.currency_exposure.map((c) => (
                <Meter key={c.currency} label={c.currency} value={c.planned_risk} max={c.cap} display={`${fmtNumber(c.planned_risk)} / ${c.cap === null ? "no cap" : fmtNumber(c.cap)}`} />
              ))}
            </div>
          )}
        </Section>
        <Section title="Instrument spread restrictions">
          {s.instruments.length === 0 ? (
            <EmptyState title="No instruments" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Symbol</TH>
                  <TH className="text-right">Spread</TH>
                  <TH className="text-right">Max spread</TH>
                  <TH className="text-right">Quote age</TH>
                  <TH>Trading</TH>
                </TR>
              </THead>
              <TBody>
                {s.instruments.map((i) => {
                  const wide = i.spread_pips !== null && i.max_spread_pips !== null && i.spread_pips > i.max_spread_pips;
                  return (
                    <TR key={i.symbol}>
                      <TD>{i.symbol}</TD>
                      <TD className={`text-right num ${wide ? "text-loss" : ""}`}>{i.spread_pips === null ? "—" : `${fmtNumber(i.spread_pips, 1)} pips`}</TD>
                      <TD className="text-right num">{i.max_spread_pips === null ? "—" : `${fmtNumber(i.max_spread_pips, 1)} pips`}</TD>
                      <TD className="text-right num">{i.quote_age_seconds === null ? "—" : `${fmtNumber(i.quote_age_seconds, 0)}s`}</TD>
                      <TD>{i.enabled ? <Badge variant="success">Enabled</Badge> : <Badge variant="muted">Disabled</Badge>}</TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </Section>
      </div>
    </div>
  );
}

function BreakerRow({ b, canClear, onClear }: { b: Breaker; canClear: boolean; onClear?: (b: Breaker) => void }) {
  const t = useTimeFormat();
  return (
    <TR>
      <TD>
        <span className="flex items-center gap-1">
          {humanize(b.type)}
          {b.critical ? <Badge variant="danger">Critical</Badge> : null}
        </span>
      </TD>
      <TD className="text-xs">
        {b.scope}
        {b.scope_key ? `: ${b.scope_key}` : ""}
      </TD>
      <TD className="max-w-80 whitespace-normal text-xs">{b.reason}</TD>
      <TD className="num text-xs">{t.dt(b.activated_at)}</TD>
      <TD className="max-w-60 whitespace-normal text-xs">
        {b.recovery ?? "—"}
        {b.auto_clear ? <Badge variant="info" className="ml-1">Auto-clear</Badge> : null}
      </TD>
      {b.active ? (
        <TD>
          {canClear && onClear ? (
            <Button size="xs" variant="outline" onClick={() => onClear(b)}>
              Clear
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">{b.critical ? "Admin only" : "—"}</span>
          )}
        </TD>
      ) : (
        <TD className="text-xs">
          <span className="num">{t.dt(b.cleared_at)}</span> {b.cleared_by ? `by ${b.cleared_by}` : ""}
          {b.clear_note ? <span className="block text-muted-foreground">{b.clear_note}</span> : null}
        </TD>
      )}
    </TR>
  );
}

function BreakersTable({ active }: { active: boolean }) {
  const qc = useQueryClient();
  const { isAdmin } = useSession();
  const [page, setPage] = React.useState(1);
  const [target, setTarget] = React.useState<Breaker | null>(null);
  const [note, setNote] = React.useState("");
  const params = { active, page, page_size: 25 };
  const q = useQuery({
    queryKey: qk.breakers(params),
    queryFn: () => riskApi.breakers(params),
    refetchInterval: active ? REFETCH.live : false,
    placeholderData: keepPreviousData,
  });
  if (q.isLoading) return <LoadingBlock rows={3} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data || q.data.items.length === 0)
    return <EmptyState title={active ? "No active circuit breakers" : "No halt history"} />;
  return (
    <>
      <Table>
        <THead>
          <TR>
            <TH>Type</TH>
            <TH>Scope</TH>
            <TH>Cause</TH>
            <TH>Activated</TH>
            <TH>Recovery</TH>
            <TH>{active ? "Action" : "Cleared"}</TH>
          </TR>
        </THead>
        <TBody>
          {q.data.items.map((b) => (
            <BreakerRow
              key={b.id}
              b={b}
              canClear={isAdmin}
              onClear={(br) => {
                setNote("");
                setTarget(br);
              }}
            />
          ))}
        </TBody>
      </Table>
      <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPageChange={setPage} />
      <ConfirmDialog
        open={!!target}
        onOpenChange={(o) => !o && setTarget(null)}
        title={`Clear breaker: ${target ? humanize(target.type) : ""}`}
        description={target ? `${target.reason}${target.critical ? " — this is a CRITICAL breaker." : ""}` : undefined}
        phrase={CLEAR_PHRASE}
        confirmLabel="Clear breaker"
        destructive
        onConfirm={async (confirm) => {
          if (!target) return;
          await riskApi.clearBreaker(target.id, confirm ?? "", note);
          await qc.invalidateQueries({ queryKey: ["risk"] });
          void qc.invalidateQueries({ queryKey: qk.status });
        }}
      >
        <div className="space-y-1">
          <Label htmlFor="clear-note">Note (why it is safe to resume)</Label>
          <Input id="clear-note" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
      </ConfirmDialog>
    </>
  );
}

function DecisionsTable() {
  const t = useTimeFormat();
  const [page, setPage] = React.useState(1);
  const [approved, setApproved] = React.useState<"" | "true" | "false">("");
  const params = { approved: approved === "" ? undefined : approved === "true", page, page_size: 25 };
  const q = useQuery({ queryKey: qk.riskDecisions(params), queryFn: () => riskApi.decisions(params), placeholderData: keepPreviousData });
  return (
    <div className="space-y-2">
      <div className="flex items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="dec-approved">Result</Label>
          <Select
            id="dec-approved"
            className="w-40"
            value={approved}
            onChange={(e) => {
              setApproved(e.target.value as "" | "true" | "false");
              setPage(1);
            }}
          >
            <option value="">All</option>
            <option value="true">Approved</option>
            <option value="false">Rejected</option>
          </Select>
        </div>
      </div>
      {q.isLoading ? (
        <LoadingBlock rows={5} />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : !q.data || q.data.items.length === 0 ? (
        <EmptyState title="No risk decisions" />
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Time</TH>
                <TH>Bot</TH>
                <TH>Symbol</TH>
                <TH>Result</TH>
                <TH className="text-right">Volume</TH>
                <TH className="text-right">Planned risk</TH>
                <TH>Reasons / checks</TH>
                <TH>Cycle</TH>
              </TR>
            </THead>
            <TBody>
              {q.data.items.map((d) => (
                <TR key={d.id}>
                  <TD className="num text-xs">{t.dt(d.created_at, true)}</TD>
                  <TD>{d.bot_name ?? "—"}</TD>
                  <TD>{d.symbol}</TD>
                  <TD>
                    <GenericStatusBadge status={d.approved ? "approved" : "rejected"} />
                  </TD>
                  <TD className="text-right num">{fmtNumber(d.volume, 2)}</TD>
                  <TD className="text-right num">
                    {fmtNumber(d.planned_risk, 2)} ({fmtPct(d.planned_risk_pct)})
                  </TD>
                  <TD className="whitespace-normal">
                    <details>
                      <summary className="cursor-pointer text-xs">
                        {d.reasons.length ? d.reasons.join("; ") : `${d.checks.filter((c) => c.passed).length}/${d.checks.length} checks passed`}
                      </summary>
                      <ul className="mt-1 space-y-0.5 text-xs">
                        {d.checks.map((c) => (
                          <li key={c.name} className="flex gap-2">
                            {c.passed ? <Badge variant="success">Pass</Badge> : <Badge variant="danger">Fail</Badge>}
                            <span>{humanize(c.name)}</span>
                            <span className="text-muted-foreground">{c.detail ?? ""}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  </TD>
                  <TD>
                    {d.cycle_id ? (
                      <Link href={`/cycles/${encodeURIComponent(d.cycle_id)}`} className="num text-xs text-primary hover:underline">
                        {d.cycle_id.slice(0, 8)}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}

function SettingsTab() {
  const { isAdmin } = useSession();
  const t = useTimeFormat();
  const cfg = useQuery({ queryKey: qk.riskConfig, queryFn: riskApi.getConfig });
  const history = useQuery({ queryKey: qk.riskHistory, queryFn: riskApi.history });
  return (
    <div className="space-y-3">
      <Section title={cfg.data ? `Risk settings (v${cfg.data.version}, updated ${t.dt(cfg.data.updated_at)}${cfg.data.updated_by ? ` by ${cfg.data.updated_by}` : ""})` : "Risk settings"}>
        {cfg.isLoading ? <LoadingBlock rows={8} /> : cfg.error ? <ErrorState error={cfg.error} onRetry={() => void cfg.refetch()} /> : cfg.data ? <RiskSettingsForm data={cfg.data} readOnly={!isAdmin} /> : null}
      </Section>
      <Section title="Change history">
        {history.isLoading ? (
          <LoadingBlock rows={3} />
        ) : history.error ? (
          <ErrorState error={history.error} />
        ) : !history.data?.length ? (
          <EmptyState title="No changes recorded" />
        ) : (
          <div className="space-y-2">
            {history.data.map((h) => (
              <details key={String(h.version)} className="rounded-md border border-border p-2">
                <summary className="cursor-pointer text-sm">
                  v{h.version} · {t.dt(h.created_at)} {h.created_by ? `· ${h.created_by}` : ""} · {h.changes?.length ?? 0} change(s)
                </summary>
                {h.changes?.length ? <DiffTable rows={h.changes} /> : null}
              </details>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

export function RiskPage() {
  const status = useQuery({ queryKey: qk.riskStatus, queryFn: riskApi.status, refetchInterval: REFETCH.live });
  return (
    <div className="space-y-3">
      <PageHeader title="Risk Management" description="All limits are enforced by the backend risk engine; this page displays and configures them." />
      <Tabs defaultValue="status">
        <TabsList>
          <TabsTrigger value="status">Status</TabsTrigger>
          <TabsTrigger value="breakers">Circuit breakers</TabsTrigger>
          <TabsTrigger value="decisions">Risk decisions</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="status" className="space-y-3">
          {status.isLoading ? <LoadingBlock rows={6} /> : status.error ? <ErrorState error={status.error} onRetry={() => void status.refetch()} /> : status.data ? <StatusPanel s={status.data} /> : null}
          <AiAnalysis subject="risk" id="current" />
        </TabsContent>
        <TabsContent value="breakers" className="space-y-3">
          <Section title="Active circuit breakers">
            <BreakersTable active />
          </Section>
          <Section title="Halt history">
            <BreakersTable active={false} />
          </Section>
        </TabsContent>
        <TabsContent value="decisions">
          <Section title="Risk decisions">
            <DecisionsTable />
          </Section>
        </TabsContent>
        <TabsContent value="settings">
          <SettingsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
