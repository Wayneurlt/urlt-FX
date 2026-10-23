import type { ApiErrorBody } from "@/types/api";

/** Same-origin base path; Next.js rewrites proxy it to the backend (see next.config.ts). */
export const API_BASE = "/api/v1";

/** Error thrown for any non-2xx API response. Carries the backend error envelope. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** Confirmation phrase the backend demands (409 `error.details.required_confirm_text`). */
  get requiredConfirmText(): string | null {
    const v = this.details?.["required_confirm_text"];
    return typeof v === "string" && v.length > 0 ? v : null;
  }

  /** Field errors for 400 responses. Accepts `details.fields`/`details.errors` maps or arrays. */
  get fieldErrors(): Record<string, string> {
    return extractFieldErrors(this.details);
  }
}

export function extractFieldErrors(details: Record<string, unknown> | null): Record<string, string> {
  if (!details) return {};
  const out: Record<string, string> = {};
  const source = details["fields"] ?? details["field_errors"] ?? details["errors"];
  if (source && typeof source === "object" && !Array.isArray(source)) {
    for (const [k, v] of Object.entries(source as Record<string, unknown>)) {
      out[k] = Array.isArray(v) ? v.map(String).join("; ") : String(v);
    }
  } else if (Array.isArray(source)) {
    for (const item of source) {
      if (item && typeof item === "object") {
        const rec = item as Record<string, unknown>;
        const loc = rec["path"] ?? rec["field"] ?? rec["loc"];
        const key = Array.isArray(loc) ? loc.join(".") : String(loc ?? "_");
        out[key] = String(rec["message"] ?? rec["msg"] ?? "invalid");
      }
    }
  }
  return out;
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Unexpected error";
}

/* ------------------------------------------------------------- CSRF */

// Kept in memory only (never localStorage). Refreshed from /auth/login and /auth/me.
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

function readCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.split("; ").find((c) => c.startsWith("fx_csrf="));
  return match ? decodeURIComponent(match.slice("fx_csrf=".length)) : null;
}

export function getCsrfToken(): string | null {
  // The cookie is the source of truth for double-submit; fall back to the in-memory copy.
  return readCsrfCookie() ?? csrfToken;
}

/* ------------------------------------------------------- Query strings */

export type QueryValue = string | number | boolean | null | undefined;

/** Builds `?a=1&b=x`, skipping undefined/null/empty-string values. Returns "" if nothing set. */
export function buildQuery(params?: Record<string, QueryValue> | object): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params as Record<string, QueryValue>)) {
    if (v === undefined || v === null || v === "") continue;
    sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/* ---------------------------------------------------------- Requests */

type Method = "GET" | "POST" | "PUT" | "DELETE" | "PATCH";

export interface RequestOptions {
  query?: Record<string, QueryValue> | object;
  body?: unknown;
  signal?: AbortSignal;
}

/** Hook invoked on any 401 so the app can redirect to /login. */
let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export async function apiRequest<T>(method: Method, path: string, opts: RequestOptions = {}): Promise<T> {
  const url = `${API_BASE}${path}${buildQuery(opts.query)}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;

  if (opts.body instanceof FormData) {
    body = opts.body; // browser sets multipart boundary
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  if (method !== "GET") {
    const token = getCsrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }

  let res: Response;
  try {
    res = await fetch(url, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Cannot reach the API server. Check that the backend is running.", null);
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const env = (data as ApiErrorBody | null)?.error;
    const err = new ApiError(
      res.status,
      env?.code ?? `http_${res.status}`,
      env?.message ?? (typeof data === "string" && data ? data : `Request failed (${res.status})`),
      env?.details ?? null,
    );
    if (res.status === 401 && unauthorizedHandler && !path.startsWith("/auth/login")) unauthorizedHandler();
    throw err;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: RequestOptions["query"], signal?: AbortSignal) =>
    apiRequest<T>("GET", path, { query, signal }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>("POST", path, { body: body ?? {} }),
  put: <T>(path: string, body?: unknown) => apiRequest<T>("PUT", path, { body: body ?? {} }),
  upload: <T>(path: string, form: FormData) => apiRequest<T>("POST", path, { body: form }),
};

/** Absolute same-origin URL for downloads (CSV exports) — used as an <a href>. */
export function apiUrl(path: string, query?: RequestOptions["query"]): string {
  return `${API_BASE}${path}${buildQuery(query)}`;
}
