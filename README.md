# Forex Trading Platform (two independent bots, simulation-first)

A modular platform with:

* **News & Volatility bot**: opens a dual BUY + SELL entry around eligible economic events or detected volatility breakouts. Each leg has its own stop. Once one leg is *confirmed* stopped out, a fixed-distance trailing stop activates on the surviving leg.
* **Daily Trading bot**: an experimental intraday trend-following baseline, with its own state machine, risk limits and records.
* **Central risk engine**: every entry from both bots goes through it. It coordinates the risk budget across processes and trips durable circuit breakers.
* **Simulated broker**: paper trading, a bar-level backtester, a validation toolkit, an MT5 adapter boundary, and a Next.js operations dashboard.

> **Safety defaults.** New installations run in **simulation** with all bots **disabled**. Starting the API or the worker never starts a bot or places an order. Live trading needs a verified MT5 connection, passing readiness checks and a typed confirmation. All prices in simulation and paper mode come from a **synthetic generator** and are labelled as such. Nothing here guarantees profit. Neither strategy has been shown to be profitable, and the bundled backtests on synthetic data lose money after costs.

## Quick start (local, no Docker)

Requires Python ≥ 3.12 and Node ≥ 22.12.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # Linux/macOS: .venv/bin/pip
(cd apps/web && npm ci)
cp .env.example .env                          # set FX_SECRET_ENCRYPTION_KEY (command inside the file)
.venv/Scripts/alembic upgrade head            # SQLite at data/fx.db by default
.venv/Scripts/python scripts/create_admin.py admin
.venv/Scripts/python scripts/seed_sample_events.py   # optional SAMPLE calendar file

make api       # http://localhost:8000/api/docs
make worker    # trading loop + job queue (required for bots, backtests, health)
make web       # http://localhost:3000  → sign in
```

Docker Compose with PostgreSQL: see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
**Next steps, integration setup and the go-live checklist: [docs/GETTING_READY.md](docs/GETTING_READY.md).**

## Common commands

| Task | Command |
|---|---|
| Migrations | `make migrate` (`alembic upgrade head`) |
| All tests | `make test` (Python: trading + API + integration, then frontend) |
| Lint / format / types | `make lint`, `make format`, `make typecheck` |
| Every quality gate | `make quality` (also `ruff format --check` and `alembic check`) |
| Paper-trading session | `make paper BOT=news-volatility` (worker must be running; `--stop` to stop) |
| Validate a config file | `python scripts/validate_config.py config/sample.yaml` |

## Using it

1. Sign in, then go to **Trading Bots** and **Enable** a bot.
2. **Manual paper trading**: open the bot page, go to the *Evaluate now* tab, and use *Submit paper entry* or *Evaluate now*. Entries pass through the full risk engine.
3. **Automated paper trading**: switch to *Paper Trading* in **Settings** and **Start** the bot. The worker runs pre-start checks, moves the bot to `running`, and evaluates it every few seconds.
4. **Backtesting**: choose a bot, period and cost model. Use synthetic data or a CSV you imported. Then run validations (out-of-sample, walk-forward, sensitivity, cost/slippage stress, drawdown Monte-Carlo, baseline).
5. **Risk Management**: limits, exposure, circuit breakers, risk decisions, and versioned settings. Loosening any limit requires typing a confirmation phrase.

## Repository layout

```
apps/web/            Next.js dashboard (App Router, TS strict, Tailwind v4, TanStack Query, RHF+Zod, Recharts)
services/api/app/    FastAPI app: api/v1 routers, auth, models, services (runtime, risk, store), workers
trading/             Pure domain library (no DB/HTTP):
  instruments/ market_data/ news/ risk/ strategies/{news_volatility,daily_trading}/
  execution/ adapters/{simulated_broker,mt5}/ backtesting/ engine/ paper_trading/ monitoring/ tests/
migrations/          Alembic
tests/integration/   cross-component tests (concurrency, reconciliation, restart)
scripts/             create_admin, seed_sample_events, paper_session, validate_config
docs/                ARCHITECTURE, TRADING_RULES, RISK_MANAGEMENT, BACKTESTING, API, SECURITY, MT5_INTEGRATION, DEPLOYMENT
```

## Verification status

* **Python:** 114 tests (unit, execution, backtesting, API, integration). Ruff, `ruff format --check`, mypy on 108 files, and `alembic check` all pass.
* **Frontend:** 32 Vitest tests, ESLint, `tsc --noEmit` and `next build` pass.
* **Manual end-to-end run:** API, worker and production web build were run against a fresh migrated database, driven through the Next.js proxy. The run covered:
  * login;
  * bot enable, start, and the worker's pre-start checks;
  * a dual-entry paper cycle with verified stops;
  * worker-executed backtests and a baseline validation;
  * refusal of a live-mode switch.

## Known limitations (read before relying on anything)

* **Market data:** no real market-data or economic-calendar vendor is integrated. Paper trading uses synthetic prices. News mode works with *sample* events only, and it blocks itself when no fresh calendar is configured.
* **MT5:** the adapter is tested only against a fake module, never against a real terminal. Docker Compose was not run in the build environment.
* **Backtester resolution:** the backtester is bar-based with conservative intrabar assumptions. Tick-accurate trailing results require importing tick data.
* **Notifications:** only in-app notifications are delivered. Email and Telegram settings can be stored and tested, but no messages are sent to them yet.
* **Price precision:** prices are floats rounded to the tick grid with Decimal before use. They are not end-to-end Decimal.
