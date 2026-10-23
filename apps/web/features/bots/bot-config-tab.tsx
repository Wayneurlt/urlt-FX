"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { botsApi } from "@/lib/api/endpoints";
import { errorMessage, isApiError } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { Section, JsonView, KeyValue } from "@/components/common/misc";
import { ErrorState, LoadingBlock } from "@/components/common/states";
import { useConfirmRetry } from "@/components/common/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession, useTimeFormat } from "@/hooks/use-auth";
import { flatten, prettyJson, setPath } from "@/lib/utils";

type Mode = "fields" | "json";

/** Parse a JSON object string; returns error message instead of throwing. */
export function parseJsonObject(text: string): { value: Record<string, unknown> | null; error: string | null } {
  try {
    const v: unknown = JSON.parse(text);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { value: null, error: "Must be a JSON object" };
    return { value: v as Record<string, unknown>, error: null };
  } catch (e) {
    return { value: null, error: `Invalid JSON: ${(e as Error).message}` };
  }
}

function ScalarField({
  path,
  value,
  onChange,
  error,
  disabled,
}: {
  path: string;
  value: unknown;
  onChange: (v: unknown) => void;
  error?: string;
  disabled?: boolean;
}) {
  const id = `cfg-${path}`;
  if (typeof value === "boolean") {
    return (
      <div className="flex items-center gap-2">
        <input id={id} type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} disabled={disabled} />
        <Label htmlFor={id} className="num">
          {path}
        </Label>
        {error ? <span className="text-xs text-loss">{error}</span> : null}
      </div>
    );
  }
  const isNumber = typeof value === "number";
  const isList = Array.isArray(value);
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="num">
        {path}
        {isList ? " (comma separated)" : ""}
      </Label>
      <Input
        id={id}
        type={isNumber ? "number" : "text"}
        step="any"
        disabled={disabled}
        aria-invalid={!!error}
        value={isList ? (value as unknown[]).join(", ") : value === null || value === undefined ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (isNumber) onChange(raw === "" ? null : Number(raw));
          else if (isList) onChange(raw.split(",").map((s) => s.trim()).filter(Boolean));
          else onChange(raw);
        }}
      />
      {error ? <p className="text-xs text-loss">{error}</p> : null}
    </div>
  );
}

export function BotConfigTab({ botId }: { botId: string }) {
  const qc = useQueryClient();
  const t = useTimeFormat();
  const { isAdmin } = useSession();
  const config = useQuery({ queryKey: qk.botConfig(botId), queryFn: () => botsApi.getConfig(botId) });
  const history = useQuery({ queryKey: qk.botConfigHistory(botId), queryFn: () => botsApi.configHistory(botId) });
  const { run, dialog } = useConfirmRetry();

  const [mode, setMode] = React.useState<Mode>("fields");
  const [draft, setDraft] = React.useState<Record<string, unknown> | null>(null);
  const [jsonText, setJsonText] = React.useState("");
  const [jsonError, setJsonError] = React.useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (config.data && draft === null) {
      setDraft(config.data.config);
      setJsonText(prettyJson(config.data.config));
    }
  }, [config.data, draft]);

  const save = useMutation({
    mutationFn: ({ cfg, confirm }: { cfg: Record<string, unknown>; confirm?: string }) => botsApi.putConfig(botId, cfg, confirm),
    onSuccess: (res) => {
      qc.setQueryData(qk.botConfig(botId), res);
      setDraft(res.config);
      setJsonText(prettyJson(res.config));
      void qc.invalidateQueries({ queryKey: qk.botConfigHistory(botId) });
      void qc.invalidateQueries({ queryKey: qk.bots });
    },
  });

  if (config.isLoading || (config.data && draft === null)) return <LoadingBlock rows={8} />;
  if (config.error) return <ErrorState error={config.error} onRetry={() => void config.refetch()} />;
  if (!config.data || !draft) return null;

  const flat = flatten(draft);

  async function onSave() {
    setSaveError(null);
    setFieldErrors({});
    setSaved(false);
    let cfg = draft!;
    if (mode === "json") {
      const parsed = parseJsonObject(jsonText);
      if (parsed.error || !parsed.value) {
        setJsonError(parsed.error);
        return;
      }
      cfg = parsed.value;
    }
    try {
      await run(
        async (confirm) => {
          await save.mutateAsync({ cfg, confirm });
          setSaved(true);
        },
        { title: "Confirm configuration change" },
      );
    } catch (e) {
      if (isApiError(e) && e.status === 400) setFieldErrors(e.fieldErrors);
      setSaveError(errorMessage(e));
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <Section
        title={`Proposed configuration (v${config.data.version})`}
        actions={
          <div className="flex gap-1" role="group" aria-label="Editor mode">
            <Button
              size="xs"
              variant={mode === "fields" ? "secondary" : "ghost"}
              onClick={() => {
                const parsed = parseJsonObject(jsonText);
                if (parsed.value) setDraft(parsed.value);
                setMode("fields");
              }}
              aria-pressed={mode === "fields"}
            >
              Fields
            </Button>
            <Button
              size="xs"
              variant={mode === "json" ? "secondary" : "ghost"}
              onClick={() => {
                setJsonText(prettyJson(draft));
                setJsonError(null);
                setMode("json");
              }}
              aria-pressed={mode === "json"}
            >
              JSON
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          {mode === "json" ? (
            <div className="space-y-1">
              <Label htmlFor="config-json">Configuration JSON</Label>
              <Textarea
                id="config-json"
                className="min-h-96"
                value={jsonText}
                disabled={!isAdmin}
                aria-invalid={!!jsonError}
                onChange={(e) => {
                  setJsonText(e.target.value);
                  setJsonError(parseJsonObject(e.target.value).error);
                }}
              />
              {jsonError ? <p className="text-xs text-loss">{jsonError}</p> : null}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {Object.entries(flat).map(([path, value]) => (
                <ScalarField
                  key={path}
                  path={path}
                  value={value}
                  disabled={!isAdmin}
                  error={fieldErrors[path] ?? fieldErrors[`config.${path}`]}
                  onChange={(v) => setDraft((d) => setPath(d ?? {}, path, v))}
                />
              ))}
              {Object.keys(flat).length === 0 ? <p className="text-sm text-muted-foreground">Configuration is empty.</p> : null}
            </div>
          )}
          {Object.keys(fieldErrors).length > 0 ? (
            <Alert variant="danger">
              <p className="font-medium">Validation errors</p>
              <ul className="mt-1 list-inside list-disc text-xs">
                {Object.entries(fieldErrors).map(([k, v]) => (
                  <li key={k}>
                    <span className="num">{k}</span>: {v}
                  </li>
                ))}
              </ul>
            </Alert>
          ) : null}
          {saveError && Object.keys(fieldErrors).length === 0 ? <Alert variant="danger">{saveError}</Alert> : null}
          {saved ? <Alert variant="success">Saved. Effective settings below are from the server response.</Alert> : null}
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>
              Updated {t.dt(config.data.updated_at)} {config.data.updated_by ? `by ${config.data.updated_by}` : ""}
            </span>
            {isAdmin ? (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setDraft(config.data!.config);
                    setJsonText(prettyJson(config.data!.config));
                    setFieldErrors({});
                    setJsonError(null);
                  }}
                >
                  Reset
                </Button>
                <Button size="sm" onClick={() => void onSave()} disabled={save.isPending || (mode === "json" && !!jsonError)}>
                  {save.isPending ? "Saving…" : "Save configuration"}
                </Button>
              </div>
            ) : null}
          </div>
        </div>
      </Section>

      <div className="space-y-3">
        <Section title="Effective settings (read-only)">
          <KeyValue items={Object.entries(flatten(config.data.effective)).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)])} />
        </Section>
        {config.data.risk_effective ? (
          <Section title="Effective risk settings (read-only)">
            <JsonView value={config.data.risk_effective} />
          </Section>
        ) : null}
        {config.data.schema ? (
          <Section title="Schema">
            <details>
              <summary className="cursor-pointer text-xs text-muted-foreground">Show JSON schema</summary>
              <JsonView value={config.data.schema} className="mt-2" />
            </details>
          </Section>
        ) : null}
        <Section title="Version history">
          {history.isLoading ? (
            <LoadingBlock rows={3} />
          ) : history.error ? (
            <ErrorState error={history.error} />
          ) : history.data && history.data.length ? (
            <Table>
              <THead>
                <TR>
                  <TH>Version</TH>
                  <TH>Created</TH>
                  <TH>By</TH>
                  <TH>Note</TH>
                </TR>
              </THead>
              <TBody>
                {history.data.map((h) => (
                  <TR key={String(h.version)}>
                    <TD className="num">v{h.version}</TD>
                    <TD className="num text-xs">{t.dt(h.created_at)}</TD>
                    <TD>{h.created_by ?? "—"}</TD>
                    <TD className="max-w-64 truncate text-xs">{h.note ?? ""}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">No history yet.</p>
          )}
        </Section>
      </div>
      {dialog}
    </div>
  );
}
