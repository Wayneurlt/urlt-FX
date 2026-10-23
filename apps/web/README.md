# urlt-FX web dashboard

Authenticated trading-operations terminal for the urlt-FX platform (frontend only).
All business logic lives in the backend; the UI only displays data and calls the REST API described in
[`docs/API.md`](../../docs/API.md).

## Stack

Next.js 15 (App Router) · React 19 · TypeScript (strict) · Tailwind CSS v4 · Radix primitives with
shadcn-style components (`components/ui`) · TanStack Query v5 · React Hook Form + Zod 4 · Recharts 3 ·
lucide-react · Vitest + Testing Library + jsdom. All versions are pinned exactly in `package.json`.

## Running

Requires Node ≥ 22.12 and npm.

```bash
cd apps/web
npm install
npm run dev          # http://localhost:3000
```

The browser only calls same-origin `/api/v1/*`. `next.config.ts` rewrites these to
`${API_ORIGIN}/api/v1/*`. `API_ORIGIN` is a **server-side** environment variable (default
`http://localhost:8000`); it is never exposed to client code.

```bash
API_ORIGIN=http://localhost:8000 npm run dev
```

Rewrites are resolved when the server is built/started, so set `API_ORIGIN` for both `npm run build` and
`npm run start` in deployments.

| Script | Purpose |
|---|---|
| `npm run dev` | Development server on port 3000 |
| `npm run build` | Production build |
| `npm run start` | Serve the production build on port 3000 |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint (flat config, `next/core-web-vitals` + `next/typescript`) |
| `npm test` | Vitest (`vitest run`) |

## Security notes

- Session auth is the backend's HttpOnly `fx_session` cookie. Nothing is stored in `localStorage`.
- Mutating requests send `X-CSRF-Token`, read from the `fx_csrf` cookie (double submit), falling back to
  the token returned by `/auth/login` / `/auth/me`, which is kept in memory only.
- Integration secrets are write-only: inputs start empty, stored values are never displayed (only a
  "Stored" badge from `is_set`), and untouched secrets are omitted from the save payload.
- Critical actions (live start, liquidation, breaker clear, risk loosening, environment switch, paper
  reset) use a typed confirmation phrase. When the API answers `409` with
  `error.details.required_confirm_text`, the UI opens a dialog that asks for that phrase and resubmits.

## Layout

```
app/                 routes (App Router). (app)/ = authenticated shell; login/ is public
components/ui/       button, card, badge, input/select/textarea, label, dialog, tabs, table, alert, skeleton
components/common/   states (loading/error/empty), labels (env/simulated/data-source badges),
                     confirm dialog + 409 confirm-retry hook, charts, tables, misc layout pieces
components/layout/   providers, app shell (sidebar + auth gate), header (status bar)
features/<area>/     page-level feature components (dashboard, bots, positions, trades, backtests,
                     risk, news, analytics, settings, integrations, notifications, auth)
hooks/               session/timezone, system status polling
lib/api/             fetch client (errors, CSRF, query strings) and typed endpoint functions
lib/                 formatting, time zone helpers, query keys
types/api.ts         TypeScript types for every entity in docs/API.md
tests/               Vitest tests (fetch is mocked)
```

## Routes

`/login`, `/` (overview), `/bots`, `/bots/[id]`, `/positions`, `/trades`, `/cycles/[id]`, `/backtests`,
`/backtests/[id]`, `/market`, `/risk`, `/analytics`, `/integrations`, `/notifications`, `/settings`.

Every route except `/login` requires a session; a `401` from `/auth/me` (or any later request) redirects to
`/login?next=…`.
