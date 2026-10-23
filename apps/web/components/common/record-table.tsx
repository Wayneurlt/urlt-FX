import * as React from "react";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState } from "./states";
import { fmtNumber, humanize, isNum } from "@/lib/utils";

/** Formats an arbitrary cell value from loosely specified API records. */
export function formatCell(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (isNum(v)) return Number.isInteger(v) ? String(v) : fmtNumber(v, Math.abs(v) < 10 ? 5 : 2);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Normalises `{key: {...}}` maps or arrays of objects into rows with a leading key column. */
export function toRows(data: unknown, keyName = "key"): Record<string, unknown>[] {
  if (Array.isArray(data)) return data.filter((r) => r && typeof r === "object") as Record<string, unknown>[];
  if (data && typeof data === "object") {
    return Object.entries(data as Record<string, unknown>).map(([k, v]) =>
      v && typeof v === "object" && !Array.isArray(v) ? { [keyName]: k, ...(v as Record<string, unknown>) } : { [keyName]: k, value: v },
    );
  }
  return [];
}

export function RecordTable({
  rows,
  columns,
  emptyText = "No rows",
  maxColumns = 14,
}: {
  rows: Record<string, unknown>[];
  columns?: string[];
  emptyText?: string;
  maxColumns?: number;
}) {
  if (!rows.length) return <EmptyState title={emptyText} />;
  const cols =
    columns ??
    Array.from(
      rows.slice(0, 20).reduce((set, r) => {
        Object.keys(r).forEach((k) => set.add(k));
        return set;
      }, new Set<string>()),
    ).slice(0, maxColumns);
  return (
    <Table>
      <THead>
        <TR>
          {cols.map((c) => (
            <TH key={c}>{humanize(c)}</TH>
          ))}
        </TR>
      </THead>
      <TBody>
        {rows.map((r, i) => (
          <TR key={i}>
            {cols.map((c) => {
              const v = r[c];
              const n = isNum(v) && /pnl|profit|return/i.test(c);
              return (
                <TD
                  key={c}
                  className={`max-w-64 truncate num text-xs ${isNum(v) ? "text-right" : ""} ${n ? (v > 0 ? "text-profit" : v < 0 ? "text-loss" : "") : ""}`}
                  title={typeof v === "object" && v !== null ? JSON.stringify(v) : undefined}
                >
                  {formatCell(v)}
                </TD>
              );
            })}
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
