# Risk management

Code: `trading/risk/` (pure engine) and `services/api/app/services/risk_service.py` (persistence, reservations, breakers).
Every entry from every bot goes through `RiskEngine.evaluate`; there is no bypass.

## Defaults (demo-testing only, not live recommendations)

| Setting | Default | Hard cap |
|---|---|---|
| News bot: combined risk of both legs per cycle | 0.5 % equity | 2 % |
| Daily bot: risk per trade | 0.25 % | 2 % |
| Global daily loss | 1.5 % of daily starting equity | 5 % |
| Max drawdown from peak | 5 % | 25 % |
| Total open risk (both bots) | 1.0 % | 5 % |
| Concurrent cycles across both bots | 2 | 10 |
| Dual-entry cycles per instrument | 1 | 3 |
| Pause after consecutive losing cycles | 3 | 10 |
| Per-currency planned risk | 0.75 % | 5 % |
| Default spread limit | 3 pips (per-instrument overrides) | 50 |
| Quote freshness | 5 s | 120 s |
| Live environment caps | cycle 0.5 %, trade 0.25 %, daily loss 1.5 % | |

Hard caps live in `HARD_LIMITS` and in the pydantic field bounds; no configuration can exceed them.
Martingale, grid recovery and loss-recovery sizing do not exist in the code base. Position size never increases
because a leg stopped out or because of previous losses.

## Sizing
`trading/risk/sizing.py`. Per leg, per lot:
`loss = |entry − stop| × contract_size × rate(quote→account) + 2 × commission + 2 × slippage × tick × contract × rate`.
Entry is the executable side (ask/bid), so the entry spread is inside the stop distance. A dual-entry cycle is
sized on the **sum** of both legs (both can stop in a whipsaw): `volume = floor_to_step(budget / Σ loss_per_lot)`.
Volumes are always rounded down; below the broker minimum the trade is rejected. Missing specs or conversion rates
block sizing (never guessed). Gap risk beyond the stop is not included and is documented as a residual risk.

## Daily loss & drawdown
`daily_loss = max(0, day_start_equity − equity)` with equity = balance + floating P/L. Realised losses move from
floating into balance without changing equity, so nothing is double counted. Reference resets at
`daily_reset_hour_utc`. New entries are rejected when `daily_loss + planned_risk > limit`.
`drawdown = (peak_equity − equity) / peak_equity`.

When a limit is reached the worker: trips a durable critical breaker (global scope) → all bots that were running
or paused become `risk_halted` → notification → applies `halt_policy` (`manage_existing` default: positions keep
their stops and trailing; `close_all`: liquidate) → every action is audited. Daily-loss and consecutive-loss
breakers clear at a real daily rollover (never on first initialisation); everything else critical needs an
administrator clear with the phrase `CLEAR` and a note. Resuming a risk-halted bot is refused while a blocking
breaker is active.

## Cross-bot coordination
Approved entries create a `risk_reservation` (planned risk, currencies, correlation group) inside the trading
lease, guarded by a CAS on `risk_state.version`. Open risk, concurrency, currency exposure and correlation-group
limits are computed from active reservations of **both** bots, so neither bot can consume the whole budget and two
processes cannot double-spend it. Reservations are released when a cycle reaches a terminal state.

## Circuit breakers
| Type | Scope | Clears |
|---|---|---|
| excessive_spread, stale_market_data | instrument | automatically |
| broker_disconnected, calendar_provider_failure | global / bot | automatically after recovery |
| daily_loss_limit, consecutive_losses | global / bot | daily rollover or admin |
| max_drawdown, uncertain_order_state, stop_placement_failure, stop_modification_failure, abnormal_slippage, repeated_order_rejection, invalid_instrument_spec, persistence_failure, duplicate_submission, reconciliation_failure | global / bot | admin only |

Each breaker records cause, timestamp, scope and recovery condition and is shown on the Risk page.

## Changing settings
Risk config is versioned (`risk_configs`) with before/after diffs and actor. Changes that loosen any limit
return 409 until resubmitted with `CONFIRM RISK INCREASE`; in live mode every change needs confirmation.
Strategy or AI proposals can never apply risk changes. The cross-checks (bot limits ≤ global limits, etc.) run on
the backend.
