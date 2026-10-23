"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { settingsApi, systemApi, tradingApi } from "@/lib/api/endpoints";
import { errorMessage, isApiError } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { FieldError, KeyValue, PageHeader, Section } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { ConfirmDialog, useConfirmRetry } from "@/components/common/confirm-dialog";
import { EnvironmentBadge, SimulatedTag } from "@/components/common/labels";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { useSystemStatus } from "@/hooks/use-system-status";
import { formatDateTime, isValidTimezone, listTimezones } from "@/lib/time";
import { fmtMoney, humanize } from "@/lib/utils";
import type { Environment, User } from "@/types/api";

function PreferencesSection() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.settings, queryFn: settingsApi.get });
  const [tz, setTz] = React.useState("");
  const [name, setName] = React.useState("");
  const zones = React.useMemo(listTimezones, []);
  React.useEffect(() => {
    if (q.data) {
      setTz(q.data.timezone);
      setName(q.data.display_name ?? "");
    }
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => settingsApi.put({ timezone: tz, display_name: name || null }),
    onSuccess: (res) => {
      qc.setQueryData(qk.settings, res);
      void qc.invalidateQueries({ queryKey: qk.me });
    },
  });
  if (q.isLoading) return <LoadingBlock rows={3} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const valid = isValidTimezone(tz);
  return (
    <form
      className="grid grid-cols-1 gap-3 md:grid-cols-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) save.mutate();
      }}
      aria-label="User preferences"
    >
      <div className="space-y-1">
        <Label htmlFor="set-tz">Display timezone</Label>
        <Select id="set-tz" value={tz} onChange={(e) => setTz(e.target.value)}>
          {!zones.includes(tz) && tz ? <option value={tz}>{tz}</option> : null}
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </Select>
        <p className="text-xs text-muted-foreground">Now: {valid ? formatDateTime(new Date().toISOString(), tz, true) : "invalid timezone"}</p>
      </div>
      <div className="space-y-1">
        <Label htmlFor="set-name">Display name</Label>
        <Input id="set-name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="flex items-end">
        <Button type="submit" size="sm" disabled={save.isPending || !valid}>
          {save.isPending ? "Saving…" : "Save preferences"}
        </Button>
      </div>
      {save.error ? <Alert variant="danger" className="md:col-span-3">{errorMessage(save.error)}</Alert> : null}
      {save.isSuccess ? <Alert variant="success" className="md:col-span-3">Saved. All times now render in {save.data.timezone}.</Alert> : null}
    </form>
  );
}

const userSchema = z.object({
  username: z.string().trim().min(3, "At least 3 characters").max(64),
  password: z.string().min(12, "At least 12 characters"),
  role: z.enum(["admin", "viewer"]),
});
type UserForm = z.infer<typeof userSchema>;

function UsersSection() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.users, queryFn: settingsApi.users });
  const { register, handleSubmit, reset, formState: { errors } } = useForm<UserForm>({
    resolver: zodResolver(userSchema),
    defaultValues: { username: "", password: "", role: "viewer" },
  });
  const create = useMutation({
    mutationFn: settingsApi.createUser,
    onSuccess: () => {
      reset();
      void qc.invalidateQueries({ queryKey: qk.users });
    },
  });
  const users: User[] = Array.isArray(q.data) ? q.data : q.data?.items ?? [];
  return (
    <div className="space-y-3">
      {q.isLoading ? (
        <LoadingBlock rows={3} />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : users.length === 0 ? (
        <EmptyState title="No users" />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Username</TH>
              <TH>Role</TH>
              <TH>Timezone</TH>
            </TR>
          </THead>
          <TBody>
            {users.map((u) => (
              <TR key={String(u.id)}>
                <TD>{u.username}</TD>
                <TD>
                  <Badge variant={u.role === "admin" ? "info" : "muted"}>{u.role}</Badge>
                </TD>
                <TD className="text-xs">{u.timezone}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <form className="grid grid-cols-1 gap-2 md:grid-cols-4" onSubmit={handleSubmit((v) => create.mutate(v))} noValidate aria-label="Create user">
        <div className="space-y-1">
          <Label htmlFor="nu-name">Username</Label>
          <Input id="nu-name" autoComplete="off" aria-invalid={!!errors.username} {...register("username")} />
          <FieldError message={errors.username?.message} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="nu-pass">Password</Label>
          <Input id="nu-pass" type="password" autoComplete="new-password" aria-invalid={!!errors.password} {...register("password")} />
          <FieldError message={errors.password?.message} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="nu-role">Role</Label>
          <Select id="nu-role" {...register("role")}>
            <option value="viewer">Viewer (read-only)</option>
            <option value="admin">Admin</option>
          </Select>
        </div>
        <div className="flex items-end">
          <Button type="submit" size="sm" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create user"}
          </Button>
        </div>
        {create.error ? <Alert variant="danger" className="md:col-span-4">{errorMessage(create.error)}</Alert> : null}
        {create.isSuccess ? <Alert variant="success" className="md:col-span-4">User created.</Alert> : null}
      </form>
    </div>
  );
}

function EnvironmentSection() {
  const qc = useQueryClient();
  const status = useSystemStatus();
  const readiness = useQuery({ queryKey: qk.liveReadiness, queryFn: systemApi.liveReadiness });
  const { run, dialog } = useConfirmRetry();
  const [target, setTarget] = React.useState<Environment>("paper");
  const [error, setError] = React.useState<string | null>(null);
  const [checks, setChecks] = React.useState<{ name: string; passed: boolean; detail: string | null }[] | null>(null);
  const [failed, setFailed] = React.useState<{ name: string; detail: string | null }[] | null>(null);

  async function submit() {
    setError(null);
    setChecks(null);
    setFailed(null);
    try {
      await run(
        async (confirm) => {
          const res = await systemApi.setEnvironment({ target, ...(confirm ? { confirm_text: confirm } : {}) });
          setChecks(res.checks);
          await qc.invalidateQueries({ queryKey: qk.status });
          void qc.invalidateQueries({ queryKey: qk.bots });
        },
        { title: `Switch environment to ${target}`, destructive: target === "live", confirmLabel: "Switch" },
      );
    } catch (e) {
      setError(errorMessage(e));
      const list = isApiError(e) ? e.details?.["checks"] : null;
      if (Array.isArray(list)) {
        setFailed(
          (list as { name: string; passed: boolean; detail: string | null }[])
            .filter((c) => !c.passed)
            .map((c) => ({ name: c.name, detail: c.detail })),
        );
      }
    } finally {
      void qc.invalidateQueries({ queryKey: qk.liveReadiness });
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        Current: <EnvironmentBadge env={status.data?.environment} />
        {status.data ? <span className="text-xs text-muted-foreground">Live trading enabled: {status.data.live_trading_enabled ? "yes" : "no"}</span> : null}
      </div>
      <div>
        <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Live readiness</p>
        {readiness.isLoading ? (
          <LoadingBlock rows={2} />
        ) : readiness.error ? (
          <ErrorState error={readiness.error} />
        ) : readiness.data ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Badge variant={readiness.data.ready ? "success" : "warning"}>{readiness.data.ready ? "Ready" : "Not ready"}</Badge>
              <Button size="xs" variant="ghost" onClick={() => void readiness.refetch()} disabled={readiness.isFetching}>
                {readiness.isFetching ? "Checking…" : "Re-check"}
              </Button>
            </div>
            <ul className="space-y-0.5 text-xs">
              {readiness.data.checks.map((c) => (
                <li key={c.name} className="flex gap-2">
                  {c.passed ? <Badge variant="success">Pass</Badge> : <Badge variant="danger">Fail</Badge>}
                  <span>{c.name}</span>
                  <span className="text-muted-foreground">{c.detail ?? ""}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="env-target">Target environment</Label>
          <Select id="env-target" className="w-44" value={target} onChange={(e) => setTarget(e.target.value as Environment)}>
            <option value="simulation">Simulation</option>
            <option value="paper">Paper trading</option>
            <option value="live">Live trading</option>
          </Select>
        </div>
        <Button size="sm" variant={target === "live" ? "destructive" : "default"} onClick={() => void submit()}>
          Switch environment
        </Button>
      </div>
      {error ? (
        <Alert variant="danger">
          {error}
          {failed && failed.length ? (
            <ul className="mt-1 space-y-0.5 text-xs">
              {failed.map((c) => (
                <li key={c.name}>
                  ✗ <span className="font-medium">{humanize(c.name)}</span>
                  {c.detail ? ` — ${c.detail}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </Alert>
      ) : null}
      {checks ? (
        <Alert variant="success">
          Environment updated.
          <ul className="mt-1 text-xs">
            {checks.map((c) => (
              <li key={c.name}>
                {c.passed ? "✓" : "✗"} {c.name} {c.detail ? `— ${c.detail}` : ""}
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}
      {dialog}
    </div>
  );
}

export const PAPER_RESET_PHRASE = "RESET PAPER ACCOUNT";

function PaperAccountSection() {
  const qc = useQueryClient();
  const t = useTimeFormat();
  const q = useQuery({ queryKey: ["paper", "account"], queryFn: tradingApi.paperAccount });
  const [balance, setBalance] = React.useState("10000");
  const [open, setOpen] = React.useState(false);
  const amount = Number(balance);
  return (
    <div className="space-y-3">
      {q.isLoading ? (
        <LoadingBlock rows={2} />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : q.data ? (
        <div className="flex flex-wrap items-center gap-3">
          <SimulatedTag simulated={q.data.simulated} />
          <KeyValue
            items={[
              ["Balance", fmtMoney(q.data.balance)],
              ["Equity", fmtMoney(q.data.equity)],
              ["Last reset", t.dt(q.data.reset_at)],
            ]}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="paper-balance">New initial balance</Label>
          <Input id="paper-balance" type="number" min={1} className="w-40" value={balance} onChange={(e) => setBalance(e.target.value)} />
        </div>
        <Button size="sm" variant="outline" disabled={!(amount > 0)} onClick={() => setOpen(true)}>
          Reset paper account
        </Button>
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Reset paper account"
        description="Clears the simulated account history. Refused while cycles are active."
        phrase={PAPER_RESET_PHRASE}
        destructive
        confirmLabel="Reset"
        onConfirm={async (confirm) => {
          await tradingApi.paperReset({ initial_balance: amount, confirm_text: confirm ?? "" });
          await qc.invalidateQueries({ queryKey: ["paper"] });
          void qc.invalidateQueries({ queryKey: qk.dashboardSummary });
        }}
      />
    </div>
  );
}

export function SettingsPage() {
  const { isAdmin, user } = useSession();
  return (
    <div className="space-y-3">
      <PageHeader title="Settings" description={`Signed in as ${user.username} (${user.role}). Preferences are stored on the server.`} />
      <Section title="Preferences">
        <PreferencesSection />
      </Section>
      {isAdmin ? (
        <>
          <Section title="Trading environment">
            <EnvironmentSection />
          </Section>
          <Section title="Paper account">
            <PaperAccountSection />
          </Section>
          <Section title="Users">
            <UsersSection />
          </Section>
        </>
      ) : null}
    </div>
  );
}
