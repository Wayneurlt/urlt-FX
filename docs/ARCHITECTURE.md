# Architecture

## Components

```
 Browser ──► apps/web (Next.js, :3000) ──/api/v1 proxy──► services/api (FastAPI, :8000) ──┐
                                                                                         │  PostgreSQL / SQLite
                     services/api worker process (python -m app.workers.runner) ─────────┤  (single source of truth)
                        ├─ trading loop: every 2s – reconcile, manage, risk halts, evaluate bots
                        └─ job loop: backtests, validations, retention (durable DB queue)
                                         │
                                         ▼
                               trading/ (pure domain library)
            strategies · risk engine · executors · simulator · MT5 adapter · backtester
```

| Layer | Location | Responsibility | Must not |
|---|---|---|---|
| Domain | `trading/` | Instruments, conversion, sizing, risk engine, strategies, cycle state machines, executors, broker adapters, backtesting. No DB, no HTTP. | Import FastAPI/SQLAlchemy; read env vars |
| Persistence & orchestration | `services/api/app/services` | Map domain records to tables, run worker ticks, reserve risk atomically, audit, notify | Contain strategy logic |
| HTTP | `services/api/app/api/v1` | Auth, validation, serialisation | Call brokers directly |
| Presentation | `apps/web` | Display & user intent; every action calls the backend and only shows confirmed state | Compute trading decisions or hold secrets |

## Key design decisions

**Durable state, never in-memory.** Cycles, legs, order intents, stop history, risk reservations, breakers,
jobs and the *simulated broker's own state* all live in the database. A restarted process rebuilds everything
from the DB and reconciles with the broker before doing anything else.

**One trading operation at a time per environment.** Worker ticks and user-triggered trading operations
(manual entry, liquidation, shutdown, paper reset) acquire the DB lease `trading:<environment>`
(`app/services/leases.py`). This serialises broker mutations across processes and makes the risk check +
reservation effectively atomic. As a second guard, risk reservations use compare-and-swap on
`risk_state.version`; a stale snapshot is rejected (`tests/integration/test_end_to_end.py` races both bots).

**Order intent before order.** `DualEntryExecutor.open` persists each leg's intent (`pending_submit`) and
commits before calling the broker. If the commit fails, a `persistence_failure` breaker trips and nothing is sent.

**Unknown outcomes are reconciled, never retried.** Timeouts/disconnects mark the intent `unknown`. The executor
queries the broker by client order id (order history, then open positions). Only after two consecutive
"not found" answers is the intent treated as `not_executed`. If the broker cannot answer, the cycle goes to
`emergency_halt` and an `uncertain_order_state` breaker blocks new entries.

**Ownership.** Every position carries the bot's `magic` number and the leg's client order id. Executors verify
both before any modify/close (`BaseExecutor.assert_owned`), so one bot can never touch another bot's positions.

**Background jobs.** A DB-backed queue (`app/workers/queue.py`) with idempotency keys, optimistic claiming
(works on SQLite and PostgreSQL), exponential-backoff retries, `dead` state for inspection and recovery of jobs
orphaned by a crashed worker. Celery/Redis was not needed; the queue is visible in the UI (`/system/jobs`).

**Environments.** `simulation` and `paper` both use the simulated broker (separate persisted accounts) and
SYNTHETIC prices; `live` uses the MT5 adapter and is only reachable through the live-readiness gate.
The environment is a persisted, audited system setting – not an env var.

## Data model (main tables)

`users, roles, user_sessions, login_attempts` · `bots, bot_configs (versioned), strategy_versions` ·
`trading_signals (unique dedupe_key)` · `trading_cycles` → `positions` (legs) → `orders` (intents), `deals`,
`stop_modifications` · `account_snapshots, sim_broker_state` · `risk_configs (versioned), risk_state,
risk_reservations, risk_decisions, circuit_breakers` · `backtest_runs, backtest_trades, backtest_metrics, datasets`
· `economic_events, instruments` · `notifications, notification_preferences, audit_logs` ·
`integration_configs, secrets (encrypted)` · `jobs, worker_heartbeats, leases, system_settings, system_health_events`.

All ids are UUID strings (bots use readable slugs). All timestamps are timezone-aware UTC
(`UTCDateTime` enforces this even on SQLite). Schema is managed by Alembic (`migrations/`).

**Retention.** Audit logs in the `strategy_evaluation` and `system` categories older than
`FX_AUDIT_RETENTION_DAYS` (default 365) are pruned by a daily job. Trading records, risk decisions,
configuration history and security/audit categories are never pruned automatically.

## Startup & recovery sequence

1. API start: create/seed reference data only (roles, bots, default configs, instruments, integrations). Never starts a bot.
2. Worker tick: acquire lease → build broker from persisted state → `process_market` (simulator) →
   **manage every active cycle** (reconcile intents, sync legs via deal history, recover trailing references,
   verify stops) → account snapshot → risk halts → market health → pre-start checks for `starting` bots →
   evaluate `running` bots.
3. Cycles interrupted in `submitting_orders` are reconciled; positions are never re-created.
