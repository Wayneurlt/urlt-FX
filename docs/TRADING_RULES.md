# Trading rules

Neither strategy is a proven profitable system. Backtests in this repository run on SYNTHETIC prices
and say nothing about real-market performance. Live trading is disabled by default.

## Bot 1 – News & Volatility (dual BUY + SELL entry)

Code: `trading/strategies/news_volatility/`, executor `trading/execution/dual_entry.py`.

### Eligibility
* **News mode** – an event qualifies if its currency is in `news.currencies`, its impact in `news.impact_levels`,
  its status is not cancelled/postponed, and *now* is inside `[scheduled − pre_event_seconds, scheduled + post_event_seconds]`.
  Instruments containing the event currency are candidates. If the calendar is missing, unavailable or older than
  `max_feed_age_minutes`, news entries are **blocked**. Sample events are labelled `origin=sample`.
* **Volatility mode** – on the last *closed* candle of `volatility.timeframe`, all of:
  ATR(atr_period) / mean ATR of the previous `atr_baseline_period` bars ≥ `atr_ratio_min`;
  last range / median range of the previous `range_lookback` bars ≥ `range_multiple_min`;
  |close − close[−expansion_lookback]| ≥ `expansion_min_pips`; optional close beyond the prior N-bar high/low;
  latest closed candle no older than `max_candle_age_bars`; per-instrument `cooldown_minutes`.
* **Dedupe** – every signal has a key (`news:<bot>:<event>:<symbol>:<window>` or `vol:<bot>:<symbol>:<candle>`) stored
  with a unique constraint; a key produces at most one cycle.

### Entry checklist (all must pass, re-checked at execution time)
bot running (or explicit manual paper entry) · instrument has a validated spec and is enabled · market open ·
broker reachable · **hedging account** (netting accounts disable the strategy; it is never silently changed) ·
risk engine approval (quote freshness, spread, stop distance vs. broker minimum, conversion available, sizing,
daily loss, drawdown, open-risk cap, concurrency, per-instrument cycle limit, currency/correlation exposure,
consecutive losses, bot daily loss, margin) · no applicable circuit breaker.

### Execution
1. BUY at ask with stop `ask − initial_stop_pips`; SELL at bid with stop `bid + initial_stop_pips` (each from its own executable side). Same volume for both legs.
2. Both intents are persisted, then submitted back-to-back. They are **not** atomic: fills, prices and outcomes may differ.
3. Unknown outcomes are reconciled with the broker (never blindly retried).
4. Every filled leg needs a broker-confirmed stop. A missing stop is (re)placed; if that fails the leg is closed and a critical `stop_placement_failure` breaker trips.
5. Exactly one leg filled → **unmatched exposure policy** (`global.unmatched_leg_policy`): `close` (default) closes it; `keep_protected` keeps it with its stop until it exits (no trailing).

### Trailing stop
Activated **only** after the opposite leg is confirmed stopped out by a broker deal with reason `sl`.
A leg closed for any other reason does not activate trailing.

* BUY: `SL = H_bid − D`, H_bid = highest bid since activation.  SELL: `SL = L_ask + D`, L_ask = lowest ask.
* D = `trailing_pips(symbol)` converted with the instrument's pip size.
* Protective-only (BUY up, SELL down), at least `min_step_pips` improvement, clamped to the broker stops level,
  skipped inside the freeze level, skipped on stale quotes.
* Rejections keep the last confirmed stop as authoritative; retries back off (2^n s, ≤ 60 s); after
  `max_modify_retries` consecutive failures a critical breaker trips. If no valid stop exists, the position is closed.
* Water mark and confirmed stop are persisted on every tick; a restarted worker resumes from them.
* A stop fill can be worse than the stop price (gaps/slippage). Both legs can stop before trailing protects anything; the real result is recorded (`outcome=both_stopped`).

### Cycle states
`created → validating → submitting_orders → both_legs_open | one_leg_filled | failed | emergency_halt`,
`both_legs_open → one_leg_stopped → trailing_active → completed`, plus `closing` (liquidation / max duration /
policy) and `rejected`. Transitions are validated (`cycle.py`) and persisted after each step.
`max_cycle_duration_minutes` closes any remaining exposure.

## Bot 2 – Daily trading (trend-following baseline, EXPERIMENTAL)

Code: `trading/strategies/daily_trading/`, executor `trading/execution/single_entry.py`. Separate config,
state machine (`created → validating → submitting → open → closing → closed`, plus `rejected/failed/emergency_halt`),
risk limits and records.

* Signals on the last **closed** `timeframe` candle only:
  LONG when `close > EMA_fast > EMA_slow` and RSI crosses above `rsi_threshold`; SHORT mirrored (crosses below `100 − threshold`).
* Stop = clamp(ATR × `stop_atr_mult`, `min_stop_pips`, `max_stop_pips`); target = ATR × `target_atr_mult`.
* Entries only inside `session.start..end` (UTC) on configured weekdays; open trades closed at `session.close_time`
  and after `max_holding_minutes`. `max_trades_per_day`, per-instrument `cooldown_minutes`.
* Optional break-even (after `trigger_r` R, stop to entry ± offset) and optional trailing (fixed distance = ATR × mult, after `activate_r` R).
* No signal → no trade. Strategies are pluggable via `STRATEGIES` in `trend_following.py`.

## Lifecycle semantics (both bots)
`pause`: no new entries, positions keep being managed. `stop`: same, bot stopped. Neither liquidates.
Orderly shutdown with liquidation is a separate action that previews affected positions and requires typing
`LIQUIDATE`. Open exposure of every bot is managed by the worker regardless of bot status.

## Manual paper trading
`POST /paper/manual-entry` (dashboard: bot page → "Evaluate now" tab) creates a MANUAL-trigger cycle through the
same risk engine and executor; "Evaluate now" runs one strategy evaluation and acts on any signal. Both are
refused in live mode. Automated paper trading = starting a bot in the `paper` environment (`make paper BOT=…`).
