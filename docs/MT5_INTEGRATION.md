# MetaTrader 5 integration

Status: **implemented but unverified against a real terminal.** The adapter (`trading/adapters/mt5/adapter.py`)
implements the full `BrokerAdapter` interface; it has not been exercised against a live MT5 terminal in this
repository. Validate it on a demo account before any live use.

## Requirements
* Windows host with an installed MT5 terminal, logged in, "Algo Trading" enabled.
* `pip install -e ".[mt5]"` (official `MetaTrader5` package – Windows only).
* The API **and** the worker must run on that Windows host (the package talks to the local terminal via IPC);
  a Linux container cannot control it. Typical deployment: Windows VM running API + worker + terminal, PostgreSQL
  elsewhere.

## Configure
1. Set `FX_SECRET_ENCRYPTION_KEY`.
2. Integrations → MetaTrader 5: login, server, optional terminal path and symbol suffix (e.g. `.r`), password.
3. Enable and **Test**. The test connects, reads the account (server, demo/real, margin mode) and disconnects.
4. Settings → Environment → Live: the readiness checks fetch broker instrument specs (they override defaults with
   `source=broker`), confirm margin mode, reconciliation and stop support. Confirm with the displayed phrase.

## Safeguards in the adapter
* Margin mode detection (`ACCOUNT_MARGIN_MODE_RETAIL_HEDGING` required for the dual-entry bot).
* Every order carries `magic` (bot) and `comment` = client order id (≤ 31 chars); `submit_market_order` first
  looks the id up and returns the existing result instead of sending twice.
* `order_send` exceptions, `None`, timeout/connection retcodes → `UnknownOrderOutcome`; the executor reconciles via
  `find_order` (history orders + open positions by comment).
* Stops validated against `trade_stops_level` / `trade_freeze_level` (converted to price via `point`).
* Deal reasons (`DEAL_REASON_SL/TP/SO`) drive stop-out confirmation for trailing activation.
* Unfinished candles are dropped from `copy_rates_range`.

## Still to verify on a demo account
Filling mode support per symbol (IOC is used), symbol suffix mapping, `history_orders_get` latency after a timeout,
partial-fill reporting, SL/TP modification on netting accounts (the dual-entry bot refuses netting anyway),
weekend/holiday session detection, reconnect behaviour of `initialize()` after terminal restarts, and pip-size
derivation for metals/indices if you add non-FX symbols.
