"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { integrationsApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { PageHeader } from "@/components/common/misc";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/common/states";
import { GenericStatusBadge } from "@/components/common/labels";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { humanize } from "@/lib/utils";
import type { Integration, IntegrationUpdate } from "@/types/api";

/**
 * Build the PUT body. Secrets are only sent when the user typed a new value;
 * omitted secrets are kept by the backend. Stored secret values are never shown.
 */
export function buildIntegrationUpdate(
  integration: Integration,
  enabled: boolean,
  settings: Record<string, string>,
  secrets: Record<string, string>,
): IntegrationUpdate {
  const outSettings: Record<string, string> = {};
  const outSecrets: Record<string, string> = {};
  for (const f of integration.fields) {
    if (f.secret) {
      const v = secrets[f.name];
      if (v) outSecrets[f.name] = v;
    } else {
      outSettings[f.name] = settings[f.name] ?? f.value ?? "";
    }
  }
  return { enabled, settings: outSettings, secrets: outSecrets };
}

export function IntegrationCard({ integration }: { integration: Integration }) {
  const qc = useQueryClient();
  const t = useTimeFormat();
  const { isAdmin } = useSession();
  const [enabled, setEnabled] = React.useState(integration.enabled);
  const [settings, setSettings] = React.useState<Record<string, string>>(() =>
    Object.fromEntries(integration.fields.filter((f) => !f.secret).map((f) => [f.name, f.value ?? ""])),
  );
  // Secret inputs always start empty — we never have (or display) stored secret values.
  const [secrets, setSecrets] = React.useState<Record<string, string>>({});

  const applyUpdate = (updated: Integration) => {
    qc.setQueryData<Integration[]>(qk.integrations, (list) => list?.map((i) => (i.key === updated.key ? updated : i)));
    void qc.invalidateQueries({ queryKey: qk.status });
  };

  const save = useMutation({
    mutationFn: () => integrationsApi.update(integration.key, buildIntegrationUpdate(integration, enabled, settings, secrets)),
    onSuccess: (updated) => {
      setSecrets({});
      applyUpdate(updated);
    },
  });
  const test = useMutation({ mutationFn: () => integrationsApi.test(integration.key), onSuccess: applyUpdate });

  return (
    <Card className="flex flex-col gap-3 p-4" data-testid={`integration-${integration.key}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">{integration.name}</h2>
          <p className="text-xs text-muted-foreground">{humanize(integration.category)}</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {integration.configured ? <Badge variant="success">Configured</Badge> : <Badge variant="muted">Not configured</Badge>}
          <GenericStatusBadge status={integration.status} />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Last check: <span className="num">{t.dt(integration.last_check)}</span>
        {integration.detail ? ` · ${integration.detail}` : ""}
      </p>

      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
        aria-label={`${integration.name} settings`}
      >
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} disabled={!isAdmin} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {integration.fields.map((f) => {
            const id = `int-${integration.key}-${f.name}`;
            return (
              <div key={f.name} className="space-y-1">
                <Label htmlFor={id} className="flex items-center gap-1.5">
                  {f.label}
                  {f.required ? <span className="text-loss" aria-label="required">*</span> : null}
                  {f.secret ? (f.is_set ? <Badge variant="success">Stored</Badge> : <Badge variant="muted">Not set</Badge>) : null}
                </Label>
                {f.secret ? (
                  <Input
                    id={id}
                    type="password"
                    autoComplete="new-password"
                    disabled={!isAdmin}
                    placeholder={f.is_set ? "Stored — leave blank to keep" : "Enter value"}
                    value={secrets[f.name] ?? ""}
                    onChange={(e) => setSecrets((s) => ({ ...s, [f.name]: e.target.value }))}
                  />
                ) : (
                  <Input
                    id={id}
                    disabled={!isAdmin}
                    value={settings[f.name] ?? ""}
                    onChange={(e) => setSettings((s) => ({ ...s, [f.name]: e.target.value }))}
                  />
                )}
              </div>
            );
          })}
        </div>
        {save.error ? <Alert variant="danger">{errorMessage(save.error)}</Alert> : null}
        {save.isSuccess ? <Alert variant="success">Saved.</Alert> : null}
        {test.error ? <Alert variant="danger">Test failed: {errorMessage(test.error)}</Alert> : null}
        {test.isSuccess ? (
          <Alert variant={test.data.status === "connected" ? "success" : "warning"}>
            Test result: {humanize(test.data.status)}
            {test.data.detail ? ` — ${test.data.detail}` : ""}
          </Alert>
        ) : null}
        {isAdmin ? (
          <div className="flex flex-wrap justify-end gap-2">
            {integration.supports_test ? (
              <Button variant="outline" size="sm" onClick={() => test.mutate()} disabled={test.isPending}>
                {test.isPending ? "Testing…" : "Test connection"}
              </Button>
            ) : null}
            <Button type="submit" size="sm" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        ) : null}
      </form>
      {integration.docs ? (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">Setup notes</summary>
          <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed">{integration.docs}</p>
        </details>
      ) : null}
    </Card>
  );
}

export function IntegrationsPage() {
  const q = useQuery({ queryKey: qk.integrations, queryFn: integrationsApi.list });
  return (
    <div>
      <PageHeader title="Integrations" description="Broker, market data, calendar, notification and AI providers. Stored secrets are never displayed." />
      {q.isLoading ? (
        <LoadingBlock rows={6} />
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.length ? (
        <EmptyState title="No integrations available" />
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {q.data.map((i) => (
            <IntegrationCard key={i.key} integration={i} />
          ))}
        </div>
      )}
    </div>
  );
}
