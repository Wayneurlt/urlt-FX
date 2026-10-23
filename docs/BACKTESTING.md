# Backtesting

Code: `trading/backtesting/`. Runs are queued via `POST /backtests`, executed by the worker, and stored with a
full config snapshot, code version (git), seed, dataset id and data fingerprint (`reproducibility`).

## Data
* **Synthetic** (default when no dataset is selected): deterministic generator (`trading/market_data/synthetic.py`,
  seed `FX_SYNTHETIC_SEED`). Multi-scale value noise plus pseudo-random volatility bursts. **Not market data** –
  every result carries a warning saying so.
* **Imported historical data**: `POST /datasets/import` (generic `time,open,high,low,close[,spread]` CSV or MT5
  export). Naive timestamps are localised with `source_tz` then converted to UTC. Files failing quality checks
  (duplicates, out-of-order, invalid OHLC, non-positive, wrong price scale, naive timezone) are rejected; gaps,
  off-grid prices and missing spread produce warnings.
* Candles are BID prices; ask = bid + spread. Tick data can be imported as M1-style rows with O=H=L=C and is then
  simulated exactly.

## Execution assumptions
1. Signals use closed bars only; fills at the next bar open (+ `latency_bars`). News entries fill at the first bar inside the event window.
2. BUY fills at ask + slippage, SELL at bid − slippage (fixed `slippage_points` + seeded uniform `random_slippage_points`). The second dual-entry leg gets `second_leg_extra_slippage_points`.
3. Long stops trigger on bid, short stops on ask. A bar opening beyond the stop fills at the open (gap), never at the stop.
4. Stop and target in the same bar → stop first. Both dual-entry stops in one bar → both stopped.
5. Trailing levels computed from a bar apply from the next bar (no intrabar optimism).
6. Take-profit fills at the target without slippage.
7. Commission per lot per side; swap per lot per 22:00 UTC rollover.
8. Rejections and partial fills are drawn from a seeded stream; an unmatched leg is closed at the next bar open.
9. Margin: entries requiring more than 50 % of equity at the configured leverage are skipped.
10. Risk: per-entry risk %, daily-loss halt (rest of day), consecutive-loss pause (until next day), one active cycle, spread limit.
11. Data gaps longer than `max_gap_bars` (weekends excluded) block entries on the bar after the gap.
12. Cross-currency P/L conversion uses the traded pair when possible, otherwise `conversion_rates` (recorded as an assumption); missing rates fail the run.

Look-ahead protection is tested (`test_no_look_ahead_future_data_does_not_change_past_trades`), and the
vectorised volatility detector is tested for equality with the live detector.

## Metrics (`trading/backtesting/metrics.py`)
A *trade* is one leg; a *cycle* is the parent (two legs for dual-entry, one for daily trades).

| Metric | Formula |
|---|---|
| Net P/L | Σ net leg P/L (gross + commission + swap) |
| Gross P/L | Σ price P/L converted to account currency (spread and slippage are inside prices) |
| Return % | net / initial balance × 100 |
| Max drawdown | largest peak-to-trough fall of the equity curve (initial + trade closes), money and % |
| Win rate | winning trades / trades (win = net > 0); cycle win rate likewise on cycles |
| Profit factor | Σ winning net / |Σ losing net|; null if either side is empty |
| Expectancy per trade / cycle | net / count |
| Average winner / loser, largest loss | over trade net P/L |
| Max consecutive losses | longest run of cycles with net ≤ 0 |
| Average holding time | mean (exit − entry) per trade |
| Exposure % | union of time in market / period |
| Costs % of gross | −(commission + swap) / |gross| × 100 |
| Sharpe / Sortino | daily equity returns, √252 annualised, only with > 20 days |
| Breakdowns | by instrument, trigger, month (exit), strategy version, exit reason |

Zero denominators return null. Fewer than 30 trades triggers a small-sample warning; a high win rate with profit
factor < 1 triggers a warning. A successful backtest never authorises live trading.

## Validation (`POST /backtests/{id}/validation`)
`in_out_sample` (chronological split), `walk_forward` (rolling folds, grid ≤ 50 combinations, choose on train,
score on following test window), `sensitivity` (one parameter, dotted path into the strategy config), `cost`
(spread multipliers), `slippage` (stress points), `drawdown` (Monte-Carlo reshuffle of trade order: median/p95/worst),
`baseline` (daily: random direction at the same times and exits; dual-entry: same cycles with trailing disabled).
