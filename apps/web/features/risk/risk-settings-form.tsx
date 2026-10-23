"use client";
import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { riskApi } from "@/lib/api/endpoints";
import { errorMessage, isApiError } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { useConfirmRetry } from "@/components/common/confirm-dialog";
import { FieldError, JsonView } from "@/components/common/misc";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { getPath, setPath } from "@/lib/utils";
import type { RiskChange, RiskConfigResponse, RiskField } from "@/types/api";

export const RISK_SECTIONS = [
  { id: "global", title: "Global" },
  { id: "news_volatility", title: "News & Volatility bot" },
  { id: "daily_trading", title: "Daily bot" },
  { id: "instruments", title: "Instrument overrides" },
  { id: "environment", title: "Environment limits" },
] as const;
type SectionId = (typeof RISK_SECTIONS)[number]["id"];

/** Assign a field to a UI section. Instrument/environment groups are inferred from section name or path prefix. */
export function sectionOf(f: RiskField): SectionId {
  const s = (f.section ?? "").toLowerCase();
  const p = f.path.toLowerCase();
  if (s.startsWith("instrument") || p.startsWith("instrument") || p.startsWith("instruments.")) return "instruments";
  if (s.startsWith("environment") || p.startsWith("environment") || p.startsWith("environments.")) return "environment";
  if (s === "news_volatility" || s === "daily_trading") return s;
  return "global";
}

type Kind = "number" | "int" | "choice" | "bool" | "text";
export function kindOf(f: RiskField, current: unknown): Kind {
  if (f.unit === "choice" || (f.choices && f.choices.length)) return "choice";
  const sample = current ?? f.default;
  if (typeof sample === "boolean") return "bool";
  if (f.unit === "count" || f.unit === "seconds") return "int";
  if (f.unit === "%" || f.unit === "pips" || typeof sample === "number" || f.min !== null || f.max !== null) return "number";
  return "text";
}

const key = (i: number) => `f${i}`;

/** Zod schema derived from RiskField min/max/choices. */
export function buildRiskSchema(fields: RiskField[], config: Record<string, unknown>) {
  const shape: Record<string, z.ZodTypeAny> = {};
  fields.forEach((f, i) => {
    const k = kindOf(f, getPath(config, f.path));
    if (k === "number" || k === "int") {
      let s = z.number({ error: `${f.label}: enter a number` }).refine(Number.isFinite, `${f.label}: enter a number`);
      if (k === "int") s = s.refine(Number.isInteger, `${f.label}: must be a whole number`);
      if (f.min !== null && f.min !== undefined) {
        const min = f.min;
        s = s.refine((v) => v >= min, `${f.label}: must be ≥ ${min}${f.unit && f.unit !== "count" ? ` ${f.unit}` : ""}`);
      }
      if (f.max !== null && f.max !== undefined) {
        const max = f.max;
        s = s.refine((v) => v <= max, `${f.label}: must be ≤ ${max}${f.unit && f.unit !== "count" ? ` ${f.unit}` : ""}`);
      }
      // Nullable fields: an empty box (NaN from valueAsNumber) means "not set" and is saved as null.
      shape[key(i)] = f.nullable
        ? z.preprocess((v) => (v === "" || v === undefined || (typeof v === "number" && Number.isNaN(v)) ? null : v), s.nullable())
        : s;
    } else if (k === "choice") {
      const choices = f.choices ?? [];
      shape[key(i)] = choices.length ? z.enum(choices as [string, ...string[]]) : z.string();
    } else if (k === "bool") {
      shape[key(i)] = z.boolean();
    } else {
      shape[key(i)] = z.string();
    }
  });
  return z.object(shape);
}

type Values = Record<string, unknown>;

function valuesFrom(fields: RiskField[], config: Record<string, unknown>): Values {
  const v: Values = {};
  fields.forEach((f, i) => {
    const cur = getPath(config, f.path);
    v[key(i)] = cur === undefined ? f.default : cur;
  });
  return v;
}

function fmtVal(v: unknown): string {
  if (v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function RiskSettingsForm({ data, readOnly }: { data: RiskConfigResponse; readOnly?: boolean }) {
  const qc = useQueryClient();
  const { run, dialog } = useConfirmRetry();
  const [result, setResult] = React.useState<{ changes: RiskChange[]; loosened: string[] } | null>(null);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [apiFieldErrors, setApiFieldErrors] = React.useState<Record<string, string>>({});

  const schema = React.useMemo(() => buildRiskSchema(data.fields, data.config), [data.fields, data.config]);
  const defaults = React.useMemo(() => valuesFrom(data.fields, data.config), [data.fields, data.config]);
  const {
    register,
    handleSubmit,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) as never, defaultValues: defaults });

  React.useEffect(() => reset(defaults), [defaults, reset]);

  const current = watch();
  const pending = data.fields
    .map((f, i) => ({ path: f.path, label: f.label, before: defaults[key(i)], after: current[key(i)] }))
    .filter((c) => JSON.stringify(c.before) !== JSON.stringify(c.after));

  const onSubmit = handleSubmit(async (values) => {
    setSaveError(null);
    setResult(null);
    setApiFieldErrors({});
    let next: Record<string, unknown> = { ...data.config };
    data.fields.forEach((f, i) => {
      next = setPath(next, f.path, values[key(i)]);
    });
    try {
      await run(
        async (confirm) => {
          const res = await riskApi.putConfig(next, confirm);
          setResult({ changes: res.changes ?? [], loosened: res.loosened ?? [] });
          await qc.invalidateQueries({ queryKey: qk.riskConfig });
          void qc.invalidateQueries({ queryKey: qk.riskHistory });
          void qc.invalidateQueries({ queryKey: qk.riskStatus });
        },
        {
          title: "Confirm risk limit increase",
          description: (
            <span className="mt-1 block text-xs">
              Loosening risk limits increases potential losses. Pending changes: {pending.map((p) => p.path).join(", ") || "—"}
            </span>
          ),
          destructive: true,
          confirmLabel: "Apply changes",
        },
      );
    } catch (e) {
      if (isApiError(e) && e.status === 400) setApiFieldErrors(e.fieldErrors);
      setSaveError(errorMessage(e));
    }
  });

  const errMsg = (i: number, path: string) =>
    ((errors[key(i)] as { message?: string } | undefined)?.message ?? apiFieldErrors[path] ?? apiFieldErrors[`config.${path}`]) || undefined;

  return (
    <>
    <form onSubmit={onSubmit} noValidate className="space-y-4" aria-label="Risk settings">
      {RISK_SECTIONS.map((section) => {
        const items = data.fields.map((f, i) => ({ f, i })).filter(({ f }) => sectionOf(f) === section.id);
        return (
          <fieldset key={section.id} className="rounded-md border border-border p-3">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{section.title}</legend>
            {items.length === 0 ? (
              <p className="text-xs text-muted-foreground">No configurable fields in this section.</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {items.map(({ f, i }) => {
                  const id = `risk-${key(i)}`;
                  const k = kindOf(f, getPath(data.config, f.path));
                  const msg = errMsg(i, f.path);
                  const hint = `${f.description}${f.description ? " · " : ""}Default ${f.nullable && f.default == null ? "none" : fmtVal(f.default)}${
                    f.min !== null || f.max !== null ? ` · Range ${f.min ?? "−∞"} – ${f.max ?? "∞"}` : ""
                  }`;
                  return (
                    <div key={f.path} className="space-y-1">
                      <Label htmlFor={id} className="flex items-center gap-1.5">
                        {f.label}
                        {f.unit && f.unit !== "choice" ? <Badge variant="muted">{f.unit}</Badge> : null}
                      </Label>
                      {k === "choice" ? (
                        <Select id={id} disabled={readOnly} aria-describedby={`${id}-hint`} {...register(key(i))}>
                          {(f.choices ?? []).map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </Select>
                      ) : k === "bool" ? (
                        <div className="flex h-9 items-center">
                          <input id={id} type="checkbox" disabled={readOnly} aria-describedby={`${id}-hint`} {...register(key(i))} />
                        </div>
                      ) : (
                        <Input
                          id={id}
                          type={k === "text" ? "text" : "number"}
                          step={k === "int" ? "1" : "any"}
                          min={f.min ?? undefined}
                          max={f.max ?? undefined}
                          disabled={readOnly}
                          aria-invalid={!!msg}
                          aria-describedby={`${id}-hint`}
                          {...register(key(i), k === "text" ? {} : { valueAsNumber: true })}
                        />
                      )}
                      <p id={`${id}-hint`} className="text-[11px] leading-snug text-muted-foreground">
                        <span className="num">{f.path}</span> · {hint}
                      </p>
                      <FieldError message={msg} />
                    </div>
                  );
                })}
              </div>
            )}
          </fieldset>
        );
      })}

      {pending.length ? (
        <div className="rounded-md border border-info/40 p-3">
          <p className="mb-1 text-xs font-semibold uppercase text-info">Pending changes ({pending.length})</p>
          <DiffTable rows={pending.map((p) => ({ path: p.path, before: p.before, after: p.after }))} />
        </div>
      ) : null}

      {saveError ? <Alert variant="danger">{saveError}</Alert> : null}
      {result ? (
        <div className="space-y-2">
          <Alert variant="success">Risk configuration saved{result.changes.length ? "" : " (no changes)"}.</Alert>
          {result.changes.length ? <DiffTable rows={result.changes} loosened={result.loosened} /> : null}
        </div>
      ) : null}

      {!readOnly ? (
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => reset(defaults)} disabled={isSubmitting}>
            Reset
          </Button>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Saving…" : "Save risk settings"}
          </Button>
        </div>
      ) : null}

      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">Hard limits (not editable)</summary>
        <JsonView value={data.hard_limits} className="mt-2" />
      </details>
    </form>
    {dialog}
    </>
  );
}

export function DiffTable({ rows, loosened = [] }: { rows: { path: string; before: unknown; after: unknown }[]; loosened?: string[] }) {
  return (
    <Table>
      <THead>
        <TR>
          <TH>Setting</TH>
          <TH>Before</TH>
          <TH>After</TH>
          <TH />
        </TR>
      </THead>
      <TBody>
        {rows.map((r) => (
          <TR key={r.path}>
            <TD className="num text-xs">{r.path}</TD>
            <TD className="num text-xs text-muted-foreground line-through">{fmtVal(r.before)}</TD>
            <TD className="num text-xs">{fmtVal(r.after)}</TD>
            <TD>{loosened.includes(r.path) ? <Badge variant="warning">Loosened</Badge> : null}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
