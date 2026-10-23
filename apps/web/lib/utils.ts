import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

const DASH = "—";

export function fmtNumber(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtMoney(v: number | null | undefined, currency?: string | null, digits = 2): string {
  if (!isNum(v)) return DASH;
  const s = fmtNumber(v, digits);
  return currency ? `${s} ${currency}` : s;
}

export function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return `${v > 0 ? "+" : ""}${fmtNumber(v, digits)}`;
}

/** Percent values from the API are already expressed in percent units (e.g. 2.5 = 2.5%). */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return `${fmtNumber(v, digits)}%`;
}

/** Win rate may be a ratio (0..1) or percent; values <= 1 are treated as ratios. */
export function fmtRatioPct(v: number | null | undefined, digits = 1): string {
  if (!isNum(v)) return DASH;
  return fmtPct(Math.abs(v) <= 1 ? v * 100 : v, digits);
}

export function fmtPrice(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  const digits = Math.abs(v) >= 50 ? 3 : 5;
  return v.toFixed(digits);
}

export function pnlClass(v: number | null | undefined): string {
  if (!isNum(v) || v === 0) return "text-muted-foreground";
  return v > 0 ? "text-profit" : "text-loss";
}

export function humanize(s: string | null | undefined): string {
  if (!s) return DASH;
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function shortId(id: string | null | undefined, n = 8): string {
  if (!id) return DASH;
  return id.length > n ? id.slice(0, n) : id;
}

export function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/** Shallow-flatten a nested object into dotted paths. */
export function flatten(obj: unknown, prefix = ""): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      const p = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flatten(v, p));
      else out[p] = v;
    }
  }
  return out;
}

export function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const key of path.split(".")) {
    if (cur && typeof cur === "object") cur = (cur as Record<string, unknown>)[key];
    else return undefined;
  }
  return cur;
}

/** Immutably set a dotted path on a plain object tree. */
export function setPath<T extends Record<string, unknown>>(obj: T, path: string, value: unknown): T {
  const keys = path.split(".");
  const root: Record<string, unknown> = { ...obj };
  let cur = root;
  keys.forEach((k, i) => {
    if (i === keys.length - 1) {
      cur[k] = value;
    } else {
      const next = cur[k];
      cur[k] = next && typeof next === "object" && !Array.isArray(next) ? { ...(next as object) } : {};
      cur = cur[k] as Record<string, unknown>;
    }
  });
  return root as T;
}
