import * as React from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { SessionProvider } from "@/hooks/use-auth";
import type { User } from "@/types/api";

export const ADMIN: User = { id: 1, username: "admin", role: "admin", timezone: "UTC" };

export interface MockCall {
  method: string;
  url: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  headers: Record<string, string>;
}

type Handler = (call: MockCall) => Response | Promise<Response>;

export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

export function apiError(status: number, code: string, message: string, details: Record<string, unknown> | null = null) {
  return json({ error: { code, message, details } }, status);
}

/**
 * Installs a fetch mock. Routes are keyed "METHOD /path" (path relative to /api/v1, without query).
 * Unmatched requests return 404 so tests never silently hit the network.
 */
export function mockFetch(routes: Record<string, Handler | object>) {
  const calls: MockCall[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const u = new URL(url, "http://localhost");
    const path = u.pathname.replace(/^\/api\/v1/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = undefined;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    } else if (init?.body) body = init.body;
    const call: MockCall = { method, url, path, query: u.searchParams, body, headers: (init?.headers ?? {}) as Record<string, string> };
    calls.push(call);
    const key = `${method} ${path}`;
    if (key in routes) {
      const r = routes[key];
      if (typeof r === "function") return (r as Handler)(call);
      return json(r);
    }
    return apiError(404, "not_found", `No mock for ${key}`);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export function renderWithProviders(ui: React.ReactElement, opts: { user?: User | null } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchInterval: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const user = opts.user === undefined ? ADMIN : opts.user;
  const wrapped = user ? <SessionProvider user={user}>{ui}</SessionProvider> : ui;
  return { client, ...render(<QueryClientProvider client={client}>{wrapped}</QueryClientProvider>) };
}

/** A promise you can resolve from the test to control when a mocked response arrives. */
export function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
