# Deployment

## Local without Docker (verified on Windows 11, Python 3.14, Node 22)
```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # .venv/bin on Linux/macOS
cd apps/web && npm ci && cd ../..
cp .env.example .env            # set FX_SECRET_ENCRYPTION_KEY (and FX_DATABASE_URL if not SQLite)
.venv/Scripts/alembic upgrade head
.venv/Scripts/python scripts/create_admin.py admin
make api      # terminal 1  (http://localhost:8000/api/docs)
make worker   # terminal 2
make web      # terminal 3  (http://localhost:3000)
```
SQLite (`data/fx.db`, WAL mode) is the zero-dependency default for one machine. Use PostgreSQL for anything shared
or long-running.

## Docker Compose (PostgreSQL)
```bash
cp .env.example .env   # set POSTGRES_PASSWORD and FX_SECRET_ENCRYPTION_KEY
docker compose up --build
docker compose run --rm api python /app/scripts/create_admin.py admin
```
Services: `db` (PostgreSQL 16), `migrate` (alembic upgrade, runs once), `api`, `worker`, `web`. Ports bind to
127.0.0.1 only. The compose file has not been run in the development environment used to build this repository
(no Docker available there) – treat it as untested until you have run it.

## Environments
| | Broker | Prices | Notes |
|---|---|---|---|
| simulation | simulated (own account) | SYNTHETIC | sandbox, manual entries |
| paper | simulated (own account) | SYNTHETIC until a data provider is added | automated forward testing |
| live | MT5 | broker | readiness gate, Windows host |

Configuration per deployment: `FX_APP_ENV=production` (forces `FX_COOKIE_SECURE=true`, disables OpenAPI docs).

## Database latency (important)
The worker makes dozens of small queries per trading tick, so **the database must be close to the API and
worker**. At ~1 ms per round trip (same region or same host) a tick takes about 2 s. At ~350 ms (a PC talking
to a database on another continent) it takes ~30 s, and stops, trailing and risk checks react that slowly.
Health reports the measured tick time as `trading_loop`. See docs/GETTING_READY.md §1 for the options.

## Production checklist
* TLS-terminating reverse proxy in front of the web app; the API should not be internet-exposed directly.
* Secret key from a secret manager; database backups; restricted DB user.
* Run exactly one worker per database (the lease makes concurrent workers safe, but one is enough).
* Monitor `/api/v1/health` (database, worker heartbeat, reconciliation age, market-data freshness, failed jobs).
* Keep the machine clock NTP-synchronised (quote freshness and windows depend on it).

## Quality gates
`make quality` → ruff lint + format check, mypy, pytest (trading, API, integration), frontend lint/typecheck/tests,
`alembic check` (models vs. migrations).
