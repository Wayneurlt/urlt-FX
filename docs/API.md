# REST API

Base path: `/api/v1`. Interactive OpenAPI docs are served at `/api/docs` (development only).

## Conventions

* **Auth**: session cookie `fx_session` (HttpOnly, SameSite=Lax, Secure in production) set by `POST /auth/login`.
* **CSRF**: every non-GET request must send header `X-CSRF-Token` equal to the `fx_csrf` cookie value
  (double-submit). The token is also returned by `/auth/login` and `/auth/me`.
* **Roles**: `admin` (full control) and `viewer` (read-only). Mutating endpoints return `403` for viewers.
* **Times**: ISO-8601 UTC strings (`2025-03-05T13:00:00+00:00`). The UI converts to the user's timezone.
* **Pagination**: `?page=1&page_size=50` → `{ "items": [...], "total": 123, "page": 1, "page_size": 50 }`.
* **Errors**: `{ "error": { "code": "string", "message": "human readable", "details": {...} | null } }` with an
  appropriate HTTP status (400 validation, 401 unauthenticated, 403 forbidden, 404, 409 conflict/state, 422 schema, 429 rate limit).
  Field-level validation errors are listed in `error.details.errors = [{loc: string[], msg}]`.
* **Data labels**: anything price- or account-related carries `simulated: bool` and/or `data_source`
  (`synthetic` | `historical` | `delayed` | `live` | `scripted`). The UI must show these labels.
* **Confirmation**: critical actions require `confirm_text` equal to a phrase the API states in the 409 response
  (`error.details.required_confirm_text`). E.g. liquidation requires `"LIQUIDATE"`.

## Authentication

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/auth/login` | `{username, password}` | `{user: User, csrf_token}` + cookies |
| POST | `/auth/logout` | – | `{ok: true}` |
| GET | `/auth/me` | – | `{user: User, csrf_token}` or 401 |

`User = {id, username, role: "admin"|"viewer", timezone}`. Repeated failed logins → 429 with `retry_after_seconds`.

## System

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | No auth. `{status: "ok"|"degraded"|"down", checks: HealthCheck[]}` |
| GET | `/system/status` | `SystemStatus` (header bar data) |
| GET | `/system/live-readiness` | `{ready: bool, checks: Check[]}` |
| POST | `/system/environment` | `{target: "simulation"|"paper"|"live", confirm_text?}` → `{environment, checks: Check[]}`; 409 if refused |
| GET | `/system/jobs?status=` | paginated `Job` |
| GET | `/system/metrics` | counters (errors, jobs, cycles) |
| GET | `/stream` | Server-sent events (`status`, `notification`); polling is the fallback |

```
HealthCheck = {name, state: "ok"|"degraded"|"down"|"unknown"|"not_configured", detail, checked_at, last_success}
Check = {name, passed: bool, detail}
SystemStatus = {
  environment: "simulation"|"paper"|"live", live_trading_enabled: bool,
  broker: {name, connected: bool, simulated: bool, detail, account_id, margin_mode},
  market_data: {provider, data_source, last_update: str|null, fresh: bool},
  calendar: {provider, available: bool, origin: "sample"|"provider"|"mock"|null, detail},
  worker: {alive: bool, last_heartbeat: str|null},
  last_reconciliation: str|null,
  active_breakers: number, unread_notifications: number,
  server_time: str
}
Job = {id, type, status: "queued"|"running"|"succeeded"|"failed"|"dead", attempts, max_attempts, error, created_at, started_at, finished_at, payload}
```

## Dashboard

`GET /dashboard/summary`
```
{
  account: {currency, balance, equity, floating_pnl, realized_pnl, daily_pnl, drawdown_pct,
            margin, free_margin, margin_level, simulated: bool, data_source, label, as_of},
  performance: {today_pnl, week_pnl, month_pnl, total_return_pct, max_drawdown_pct,
                win_rate, profit_factor, average_trade, completed_trades, completed_cycles,
                insufficient: string[]},   // metric names without enough history (value is null)
  open_positions: number, active_cycles: number,
  bots: BotBrief[],             // {id, name, kind, status, daily_pnl}
  active_breakers: Breaker[],
  recent_alerts: Notification[],
  recent_executions: Execution[], // {time, cycle_id, bot_name, symbol, side, kind: "fill"|"rejected"|"close", price, volume, detail}
  upcoming_events: NewsEvent[],
  health: HealthCheck[]
}
```

`GET /dashboard/charts?start=&end=`
```
{
  equity: [{time, equity, balance}],
  daily_pnl: [{date, pnl}],
  drawdown: [{time, drawdown_pct}],
  by_bot: [{bot_id, name, net_pnl, cycles}],
  by_instrument: [{symbol, net_pnl, trades}],
  recent_outcomes: [{cycle_id, bot_name, symbol, net_pnl, closed_at, outcome}]
}
```

## Bots

```
Bot = {
  id, name, kind: "news_volatility"|"daily_trading", strategy_name, strategy_version,
  status: "disabled"|"stopped"|"starting"|"running"|"paused"|"risk_halted"|"error",
  enabled: bool, environment, instruments: string[], timeframe: string|null,
  max_risk_pct: number, active_cycles, open_positions, daily_pnl, lifetime_pnl,
  last_evaluation_at, last_execution_at, last_error, magic, config_version,
  allowed_actions: string[]          // computed from the backend state machine
}
```

| Method | Path | Body / Notes |
|---|---|---|
| GET | `/bots` | `Bot[]` |
| GET | `/bots/{id}` | `Bot` |
| POST | `/bots/{id}/actions/{action}` | action ∈ `enable, disable, start, pause, resume, stop`; body `{confirm_text?}` (live start needs `"START LIVE"`). Returns `Bot`; 409 for invalid transitions |
| GET | `/bots/{id}/shutdown-preview` | `{cycles: Cycle[], positions: Position[], policy}` |
| POST | `/bots/{id}/shutdown` | `{liquidate: bool, confirm_text?}` (`"LIQUIDATE"` when liquidate) → `{bot: Bot, closed_cycles: string[]}` |
| GET | `/bots/{id}/config` | `{version, config, effective, risk_effective, updated_at, updated_by, schema}` |
| PUT | `/bots/{id}/config` | `{config, confirm_text?}` → same shape as GET; 400 with field errors |
| GET | `/bots/{id}/config/history` | `[{version, config, created_at, created_by, note}]` |
| GET | `/bots/{id}/evaluations?page=` | paginated `{id, time, symbol, mode, result: "signal"|"no_signal"|"blocked", reason, details}` plus `latest` (most recent diagnostics) |
| POST | `/bots/{id}/evaluate-now` | runs one evaluation tick now (simulation/paper only) → `{signals, diagnostics, blocked, entries: [{cycle_id, approved, reasons}]}` |

## Positions, cycles and trades

```
Position = {cycle_id, leg_id, bot_id, bot_name, symbol, side, volume, entry_price, current_price,
            stop_loss, take_profit, floating_pnl, opened_at, trailing_active, simulated, environment}
Leg = {id, side, client_order_id, intent_status, status, requested_volume, filled_volume, position_id,
       broker_order_id, fill_price, fill_time, intended_sl, confirmed_sl, tp, exit_price, exit_time,
       exit_reason, gross_pnl, commission, swap, net_pnl, floating_pnl, slippage_pips, reject_reason,
       stop_history: [{time, action, sl, previous?, reason?}]}   // action: initial_confirmed | trailing_update | break_even | rejected | broker_reported | closed
Cycle = {id, kind: "dual_entry"|"single", bot_id, bot_name, strategy_version, trigger_type, event_id, symbol,
         environment, state, volume, planned_risk, realized_pnl, outcome, created_at, updated_at, closed_at,
         trailing_activated_at, simulated, legs: Leg[], notes: string[], error}
CycleDetail = Cycle & {risk_decision, signal, config_snapshot, trailing, events: LogEntry[]}
TradeRow = Leg & {cycle_id, bot_id, bot_name, symbol, strategy_version, trigger_type, environment, cycle_state, simulated}
```

| Method | Path | Notes |
|---|---|---|
| GET | `/positions` | `Position[]` (open legs only) |
| GET | `/cycles?bot_id=&state=&active=&symbol=&page=&page_size=` | paginated `Cycle` |
| GET | `/cycles/{id}` | `CycleDetail` |
| POST | `/cycles/{id}/close` | `{confirm_text: "LIQUIDATE"}` authorised liquidation → `{cycle_id, state, realized_pnl}` |
| GET | `/trades?start=&end=&bot_id=&strategy_version=&symbol=&side=&environment=&trigger=&outcome=&status=&cycle_id=&sort=&page=&page_size=` | paginated `TradeRow`; `outcome` ∈ `win, loss, open` |
| GET | `/trades/export.csv?...` | same filters, CSV |
| POST | `/paper/manual-entry` | `{bot_id, symbol, side?: "BUY"|"SELL", stop_pips, target_pips?}` – goes through the risk engine; simulation/paper only |
| GET | `/paper/account` | simulated account `{balance, equity, ..., simulated: true, reset_at}` |
| POST | `/paper/reset` | `{initial_balance, confirm_text: "RESET PAPER ACCOUNT"}` – refuses while cycles are active |

## Risk

| Method | Path | Notes |
|---|---|---|
| GET | `/risk/config` | `{config, hard_limits, fields: RiskField[], updated_at, updated_by, version}` |
| PUT | `/risk/config` | `{config, confirm_text?}`. Loosening any limit returns 409 requiring `"CONFIRM RISK INCREASE"` → `{config, changes: [{path, before, after}], loosened: string[]}` |
| GET | `/risk/config/history` | `[{version, config, created_at, created_by, changes}]` |
| GET | `/risk/status` | `RiskStatus` |
| GET | `/risk/decisions?approved=&bot_id=&page=` | paginated `RiskDecision` |
| GET | `/risk/breakers?active=true|false&page=` | paginated `Breaker` |
| POST | `/risk/breakers/{id}/clear` | `{confirm_text: "CLEAR", note}`; critical breakers are admin-only |

```
RiskField = {path, section: "global"|"news_volatility"|"daily_trading", label, description, unit: "%"|"count"|"pips"|"seconds"|"choice"|"",
             default, min, max, choices?: string[]}
RiskStatus = {currency, equity, day_start_equity, daily_loss, daily_loss_limit, daily_loss_used_pct,
              drawdown_pct, drawdown_limit_pct, open_risk, open_risk_cap, active_cycles, max_concurrent_cycles,
              currency_exposure: [{currency, planned_risk, cap}],
              instruments: [{symbol, spread_pips|null, max_spread_pips, enabled, quote_age_seconds|null}],
              halted: bool, halt_reasons: string[], blocked_attempts_today: number}
RiskDecision = {id, created_at, bot_id, bot_name, symbol, approved, volume, planned_risk, planned_risk_pct,
                checks: [{name, passed, detail}], reasons: string[], cycle_id}
Breaker = {id, type, scope: "global"|"bot"|"instrument", scope_key, reason, critical, auto_clear, recovery,
           activated_at, cleared_at, cleared_by, clear_note, active}
```

## Backtests and datasets

```
BacktestRequest = {
  bot_kind, symbol, base_timeframe: "M1", start, end, initial_balance, account_currency,
  dataset_id: string|null,        // null → synthetic generator (labelled SYNTHETIC)
  seed, strategy: {...},           // bot config fields (see /bots/{id}/config schema)
  costs: {spread_mode, fixed_spread_pips, spread_multiplier, slippage_points, random_slippage_points,
          second_leg_extra_slippage_points, commission_per_lot_side, swap_long_per_lot_day, swap_short_per_lot_day},
  execution: {latency_bars, reject_probability, partial_fill_probability, partial_fill_ratio, leverage},
  risk: {risk_per_entry_pct, max_daily_loss_pct, max_consecutive_losses, max_spread_pips},
  conversion_rates: {}
}
BacktestRun = {id, status: "queued"|"running"|"succeeded"|"failed", progress: 0..1, error, created_at, finished_at,
               bot_kind, symbol, start, end, data_source, summary: {net_pnl, return_pct, max_drawdown_pct, trade_count, ...}|null}
BacktestDetail = BacktestRun & {config, metrics, warnings: string[], assumptions: string[], counters,
               equity_curve: [{time, equity}], daily_equity: [{time, equity}], data_fingerprint, reproducibility: {...},
               validation: {[kind]: result}}
```

| Method | Path | Notes |
|---|---|---|
| POST | `/backtests` | `BacktestRequest` → `BacktestRun` (queued job) |
| GET | `/backtests?bot_kind=&page=` | paginated `BacktestRun`, optionally for one bot kind |
| GET | `/backtests/{id}` | `BacktestDetail` |
| GET | `/backtests/{id}/trades?page=` | paginated trade ledger |
| GET | `/backtests/{id}/cycles?page=` | paginated cycle ledger |
| GET | `/backtests/{id}/export.csv` | trades CSV |
| POST | `/backtests/{id}/validation` | `{kind: "in_out_sample"|"walk_forward"|"sensitivity"|"cost"|"slippage"|"drawdown"|"baseline", params}` → queued |
| GET | `/datasets` | `[{id, symbol, timeframe, rows, start, end, source, quality, created_at}]` |
| POST | `/datasets/import` | multipart: `file`, `symbol`, `timeframe`, `source_tz` → dataset + quality report |

## Market and news

| Method | Path | Notes |
|---|---|---|
| GET | `/market/instruments` | instrument specs with `source` (default/broker/user) |
| GET | `/market/quotes` | `[{symbol, bid, ask, spread_pips, time, data_source, fresh}]` |
| GET | `/market/candles?symbol=&timeframe=&start=&end=` | `{data_source, candles: [{time, open, high, low, close, spread}]}` |
| GET | `/news/events?start=&end=` | `[{id, event_id, currency, name, scheduled_at, impact, forecast, previous, actual, provider, origin, status, processed}]` |
| GET | `/news/provider` | `{provider, available, origin, detail, last_fetch}` |

## Integrations and AI

```
Integration = {key, name, category: "broker"|"market_data"|"calendar"|"notifications"|"ai",
               configured: bool, enabled: bool, status: "connected"|"disconnected"|"unavailable"|"unverified"|"not_configured",
               last_check: str|null, detail, fields: [{name, label, secret: bool, required: bool, value: string|null, is_set: bool}],
               docs: string, supports_test: bool}
```
Secret values are never returned; `value` is `null` for secrets and `is_set` says whether one is stored.

| Method | Path | Notes |
|---|---|---|
| GET | `/integrations` | `Integration[]` |
| PUT | `/integrations/{key}` | `{enabled, settings: {}, secrets: {}}` (omit a secret to keep it) |
| POST | `/integrations/{key}/test` | performs a real check → `Integration` |
| POST | `/ai/analyze` | `{subject: "backtest"|"cycle"|"risk", id}` → `{provider, model, content, label, generated_at}`; 409 if no AI provider configured |

## Notifications, logs, settings

| Method | Path | Notes |
|---|---|---|
| GET | `/notifications?unread=&page=` | paginated `{id, created_at, severity, kind, title, message, bot_id, cycle_id, read}` |
| POST | `/notifications/{id}/read` · `/notifications/read-all` | |
| GET/PUT | `/notifications/preferences` | `{kinds: {[kind]: {in_app: bool}}, channels: {...}}` |
| GET | `/logs?severity=&category=&bot_id=&cycle_id=&correlation_id=&q=&page=` | paginated `LogEntry` |
| GET | `/logs/{id}` | `LogEntry` with `data` |
| GET/PUT | `/settings` | `{timezone, display_name}` (per user) |
| GET | `/users` · POST `/users` | admin only: `{username, password, role}` |

```
LogEntry = {id, created_at, severity: "debug"|"info"|"warning"|"error"|"critical", category, message,
            correlation_id, bot_id, cycle_id, actor, data}
```
