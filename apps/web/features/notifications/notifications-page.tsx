"use client";
import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck } from "lucide-react";
import { logsApi, notificationsApi, systemApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/client";
import { qk, REFETCH } from "@/lib/query-keys";
import { JsonView, KeyValue, PageHeader, Pagination, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { GenericStatusBadge, SeverityBadge } from "@/components/common/labels";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useTimeFormat } from "@/hooks/use-auth";
import { humanize } from "@/lib/utils";
import type { LogFilters, NotificationPreferences } from "@/types/api";

function NotificationsList() {
  const qc = useQueryClient();
  const t = useTimeFormat();
  const [page, setPage] = React.useState(1);
  const [unreadOnly, setUnreadOnly] = React.useState(false);
  const params = { unread: unreadOnly ? true : undefined, page, page_size: 25 };
  const q = useQuery({ queryKey: qk.notifications(params), queryFn: () => notificationsApi.list(params), placeholderData: keepPreviousData, refetchInterval: REFETCH.slow });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["notifications"] });
    void qc.invalidateQueries({ queryKey: qk.status });
  };
  const markRead = useMutation({ mutationFn: notificationsApi.markRead, onSuccess: refresh });
  const readAll = useMutation({ mutationFn: notificationsApi.readAll, onSuccess: refresh });
  return (
    <Section
      title="Notifications"
      actions={
        <>
          <label className="flex items-center gap-1.5 text-xs">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => {
                setUnreadOnly(e.target.checked);
                setPage(1);
              }}
            />
            Unread only
          </label>
          <Button size="xs" variant="outline" onClick={() => readAll.mutate()} disabled={readAll.isPending}>
            <CheckCheck />
            Mark all read
          </Button>
        </>
      }
    >
      {markRead.error || readAll.error ? <Alert variant="danger">{errorMessage(markRead.error ?? readAll.error)}</Alert> : null}
      {q.isLoading ? (
        <LoadingBlock rows={6} />
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title={unreadOnly ? "No unread notifications" : "No notifications"} />
      ) : (
        <>
          <ul className="divide-y divide-border/60">
            {q.data.items.map((n) => (
              <li key={n.id} className={`flex flex-wrap items-start gap-2 py-2 ${n.read ? "opacity-70" : ""}`}>
                <SeverityBadge severity={n.severity} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {!n.read ? <span className="mr-1 inline-block size-1.5 rounded-full bg-primary align-middle" aria-label="unread" /> : null}
                    {n.title}
                  </p>
                  <p className="text-xs text-muted-foreground">{n.message}</p>
                  <p className="mt-0.5 flex gap-2 text-xs">
                    <span className="text-muted-foreground">{humanize(n.kind)}</span>
                    {n.bot_id ? (
                      <Link className="text-primary hover:underline" href={`/bots/${encodeURIComponent(n.bot_id)}`}>
                        bot
                      </Link>
                    ) : null}
                    {n.cycle_id ? (
                      <Link className="text-primary hover:underline" href={`/cycles/${encodeURIComponent(n.cycle_id)}`}>
                        cycle {n.cycle_id.slice(0, 8)}
                      </Link>
                    ) : null}
                  </p>
                </div>
                <span className="num text-xs text-muted-foreground">{t.dt(n.created_at)}</span>
                {!n.read ? (
                  <Button size="xs" variant="ghost" onClick={() => markRead.mutate(n.id)} disabled={markRead.isPending}>
                    Mark read
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
          <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPageChange={setPage} />
        </>
      )}
    </Section>
  );
}

function PreferencesPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.notificationPrefs, queryFn: notificationsApi.preferences });
  const [draft, setDraft] = React.useState<NotificationPreferences | null>(null);
  React.useEffect(() => {
    if (q.data) setDraft(q.data);
  }, [q.data]);
  const save = useMutation({
    mutationFn: (p: NotificationPreferences) => notificationsApi.savePreferences(p),
    onSuccess: (res) => qc.setQueryData(qk.notificationPrefs, res),
  });
  if (q.isLoading || (q.data && !draft)) return <LoadingBlock rows={4} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!draft) return null;
  const kinds = Object.entries(draft.kinds);
  return (
    <Section title="Notification preferences">
      {kinds.length === 0 ? (
        <EmptyState title="No notification kinds" />
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {kinds.map(([kind, pref]) => (
            <label key={kind} className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5 text-sm">
              <input
                type="checkbox"
                checked={!!pref.in_app}
                onChange={(e) => setDraft((d) => (d ? { ...d, kinds: { ...d.kinds, [kind]: { ...d.kinds[kind], in_app: e.target.checked } } } : d))}
              />
              {humanize(kind)} <span className="text-xs text-muted-foreground">(in-app)</span>
            </label>
          ))}
        </div>
      )}
      {Object.keys(draft.channels ?? {}).length ? (
        <div className="mt-3">
          <p className="mb-1 text-xs text-muted-foreground">Channels (configured via Integrations)</p>
          <JsonView value={draft.channels} className="max-h-40" />
        </div>
      ) : null}
      {save.error ? <Alert variant="danger" className="mt-2">{errorMessage(save.error)}</Alert> : null}
      {save.isSuccess ? <Alert variant="success" className="mt-2">Preferences saved.</Alert> : null}
      <div className="mt-3 flex justify-end">
        <Button size="sm" onClick={() => save.mutate(draft)} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save preferences"}
        </Button>
      </div>
    </Section>
  );
}

function LogDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const t = useTimeFormat();
  const q = useQuery({ queryKey: qk.log(id ?? ""), queryFn: () => logsApi.get(id!), enabled: !!id });
  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()}>
      <DialogContent side="right">
        <DialogHeader>
          <DialogTitle>Log entry</DialogTitle>
          <DialogDescription>Full structured record</DialogDescription>
        </DialogHeader>
        {q.isLoading ? (
          <LoadingBlock rows={6} />
        ) : q.error ? (
          <ErrorState error={q.error} />
        ) : q.data ? (
          <div className="space-y-3">
            <KeyValue
              items={[
                ["Time", t.dt(q.data.created_at, true)],
                ["Severity", <SeverityBadge key="s" severity={q.data.severity} />],
                ["Category", q.data.category],
                ["Message", <span key="m" className="font-sans">{q.data.message}</span>],
                ["Correlation", q.data.correlation_id],
                ["Bot", q.data.bot_id],
                ["Cycle", q.data.cycle_id ? <Link key="c" className="text-primary hover:underline" href={`/cycles/${encodeURIComponent(q.data.cycle_id)}`}>{q.data.cycle_id}</Link> : null],
                ["Actor", q.data.actor],
              ]}
            />
            <JsonView value={q.data.data} />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

const EMPTY_LOG_FILTERS = { severity: "", category: "", bot_id: "", cycle_id: "", correlation_id: "", q: "" };

function LogsPanel() {
  const t = useTimeFormat();
  const [form, setForm] = React.useState(EMPTY_LOG_FILTERS);
  const [applied, setApplied] = React.useState(EMPTY_LOG_FILTERS);
  const [page, setPage] = React.useState(1);
  const [openId, setOpenId] = React.useState<string | null>(null);
  const filters: LogFilters = { ...applied, page, page_size: 50 };
  const q = useQuery({ queryKey: qk.logs(filters), queryFn: () => logsApi.list(filters), placeholderData: keepPreviousData });
  const set = (k: keyof typeof EMPTY_LOG_FILTERS) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  return (
    <Section title="Logs">
      <form
        className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ ...form });
          setPage(1);
        }}
        aria-label="Log filters"
      >
        <div className="space-y-1">
          <Label htmlFor="lg-sev">Severity</Label>
          <Select id="lg-sev" value={form.severity} onChange={set("severity")}>
            <option value="">Any</option>
            {["debug", "info", "warning", "error", "critical"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="lg-cat">Category</Label>
          <Input id="lg-cat" value={form.category} onChange={set("category")} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="lg-bot">Bot ID</Label>
          <Input id="lg-bot" value={form.bot_id} onChange={set("bot_id")} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="lg-cycle">Cycle ID</Label>
          <Input id="lg-cycle" value={form.cycle_id} onChange={set("cycle_id")} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="lg-corr">Correlation ID</Label>
          <Input id="lg-corr" value={form.correlation_id} onChange={set("correlation_id")} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="lg-q">Search</Label>
          <Input id="lg-q" value={form.q} onChange={set("q")} />
        </div>
        <div className="flex items-end gap-2">
          <Button type="submit" size="sm">
            Apply
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setForm(EMPTY_LOG_FILTERS);
              setApplied(EMPTY_LOG_FILTERS);
              setPage(1);
            }}
          >
            Reset
          </Button>
        </div>
      </form>
      {q.isLoading ? (
        <LoadingBlock rows={8} />
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title="No log entries match" />
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Time</TH>
                <TH>Severity</TH>
                <TH>Category</TH>
                <TH>Message</TH>
                <TH>Correlation</TH>
                <TH>Actor</TH>
              </TR>
            </THead>
            <TBody>
              {q.data.items.map((l) => (
                <TR key={l.id} className="cursor-pointer" onClick={() => setOpenId(l.id)}>
                  <TD className="num text-xs">{t.dt(l.created_at, true)}</TD>
                  <TD>
                    <SeverityBadge severity={l.severity} />
                  </TD>
                  <TD className="text-xs">{l.category}</TD>
                  <TD className="max-w-[28rem] truncate">
                    <button type="button" className="text-left hover:underline" onClick={() => setOpenId(l.id)}>
                      {l.message}
                    </button>
                  </TD>
                  <TD className="num text-xs">{l.correlation_id?.slice(0, 12) ?? "—"}</TD>
                  <TD className="text-xs">{l.actor ?? "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPageChange={setPage} />
        </>
      )}
      <LogDrawer id={openId} onClose={() => setOpenId(null)} />
    </Section>
  );
}

function JobsPanel() {
  const t = useTimeFormat();
  const [status, setStatus] = React.useState("");
  const [page, setPage] = React.useState(1);
  const params = { status: status || undefined, page, page_size: 25 };
  const q = useQuery({ queryKey: qk.jobs(params), queryFn: () => systemApi.jobs(params), placeholderData: keepPreviousData, refetchInterval: REFETCH.slow });
  return (
    <Section
      title="Jobs"
      actions={
        <div className="flex items-center gap-1.5">
          <Label htmlFor="job-status">Status</Label>
          <Select
            id="job-status"
            className="h-7 w-32 text-xs"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Any</option>
            {["queued", "running", "succeeded", "failed", "dead"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
      }
    >
      {q.isLoading ? (
        <LoadingBlock rows={5} />
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title="No jobs" />
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Created</TH>
                <TH>Type</TH>
                <TH>Status</TH>
                <TH className="text-right">Attempts</TH>
                <TH>Started</TH>
                <TH>Finished</TH>
                <TH>Error</TH>
              </TR>
            </THead>
            <TBody>
              {q.data.items.map((j) => (
                <TR key={j.id}>
                  <TD className="num text-xs">{t.dt(j.created_at, true)}</TD>
                  <TD>{humanize(j.type)}</TD>
                  <TD>
                    <GenericStatusBadge status={j.status} />
                  </TD>
                  <TD className="text-right num">
                    {j.attempts}/{j.max_attempts}
                  </TD>
                  <TD className="num text-xs">{t.dt(j.started_at, true)}</TD>
                  <TD className="num text-xs">{t.dt(j.finished_at, true)}</TD>
                  <TD className="max-w-72 truncate text-xs text-loss" title={j.error ?? undefined}>
                    {j.error ?? ""}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPageChange={setPage} />
        </>
      )}
    </Section>
  );
}

export function NotificationsPage() {
  return (
    <div className="space-y-3">
      <PageHeader title="Notifications & Logs" />
      <Tabs defaultValue="notifications">
        <TabsList>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
          <TabsTrigger value="preferences">Preferences</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="jobs">Jobs</TabsTrigger>
        </TabsList>
        <TabsContent value="notifications">
          <NotificationsList />
        </TabsContent>
        <TabsContent value="preferences">
          <PreferencesPanel />
        </TabsContent>
        <TabsContent value="logs">
          <LogsPanel />
        </TabsContent>
        <TabsContent value="jobs">
          <JobsPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
