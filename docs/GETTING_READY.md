# Getting ready: next steps and integration setup

Work through these in order. Each stage should be solid before the next one starts. Nothing here makes either
strategy profitable. These steps make the *platform* trustworthy enough to find out whether a strategy is worth
anything.

## 1. Fix the database distance first (blocking)

The app currently runs on your PC and the database is Neon in AWS **us-east-2**. Each query takes about 350 ms
round trip, and one trading-loop tick makes dozens of queries, so a tick takes about **30 s instead of 2 s**. In
practice, stop-loss checks, trailing-stop updates and risk checks react up to 30 s late, and every screen takes
several seconds to load. Health shows this as `trading_loop: degraded`.

Pick one:

| Option | When | What to do |
|---|---|---|
| **A. Run the app next to the database** (recommended) | You want Neon and a deployment others can reach | Run API + worker (+ web) on a server in **AWS us-east-2** (e.g. a small EC2/Lightsail VM). For MT5 later this must be a **Windows** VM (see §4). |
| B. Move the database next to the app | You'll keep running on this PC | Create a Neon project in the region closest to you, or run PostgreSQL locally. A different region still adds tens of ms per query; local is best. |
| C. Local SQLite for now | Solo development / paper testing on this PC | Set `FX_DATABASE_URL=` to empty in `.env` (defaults to `data/fx.db`), run `alembic upgrade head` and `scripts/create_admin.py admin` again. |

Done when Health shows `trading_loop` around 2–3 s per tick.

## 2. Housekeeping (15 minutes)

1. **Change the admin password**: `.venv/Scripts/python scripts/create_admin.py admin`.
2. **Rotate the Neon password** (it was shared in chat) in the Neon console and update `FX_DATABASE_URL` in `.env`.
3. **Back up `FX_SECRET_ENCRYPTION_KEY`** from `.env` to a password manager. If it is lost, every stored integration credential must be re-entered.
4. Need others to watch? Settings → Users → create a **viewer** (read-only) account. Don't share the admin.
5. Settings → set your **timezone**, so all times display in local time (they are stored in UTC).

## 3. Learn each bot in its own workspace

The sidebar's **Workspace** switcher selects *Portfolio (both bots)*, *News & Volatility* (violet) or
*Daily Trading* (teal). Inside a workspace, every page shows only that bot's data. Risk, Market,
Integrations, Notifications and Settings stay shared, because the account and risk budget are shared.

For each bot:
1. **Configuration**: read every setting; change one thing at a time; each save creates a new version (history at the bottom).
2. **Activity → Evaluate now**: runs the strategy once and shows exactly why it did or didn't signal.
3. **Activity → Manual paper entry**: places a simulated entry through the real risk engine; open the cycle to see both legs, stops and the risk checks.
4. **Enable → Start**: the worker runs pre-start checks, then evaluates the bot automatically.
5. **Risk Management**: understand the limits before loosening any (loosening needs a typed confirmation).

## 4. Integrations: step by step

All integrations live on the **Integrations** page. Each card has the same flow:
**fill fields → tick Enabled → Save → Test**. A card only shows **connected** after a real test succeeds.
Saving alone shows *unverified*. Secrets are encrypted with `FX_SECRET_ENCRYPTION_KEY` and never shown again,
only "stored".

### 4.1 Market data: nothing to do
The built-in provider is the **synthetic** generator (labelled *SYNTHETIC PRICES* everywhere). Real prices arrive
only through the MT5 connection in live mode (§4.5). To backtest on real history, see §5.

### 4.2 Economic calendar (News mode)
No real calendar vendor is built in yet; news entries stay blocked until a calendar is configured.

1. Create sample events:
   - for backtests: `.venv/Scripts/python scripts/seed_sample_events.py`
   - to watch the news flow in paper trading: `... seed_sample_events.py --relative 30` (adds three SAMPLE events 30, 45 and 60 minutes from now)
2. Integrations → **Economic calendar**: Provider = `sample`, leave Path empty (uses `data/sample_events.json`), tick **Enabled**, **Save**, **Test**.
3. Market & News shows the events with a **SAMPLE DATA** badge. They are not real news.
4. A real vendor (e.g. a paid calendar API) needs a small adapter in `trading/news/provider.py`. Ask when you choose one.

### 4.3 AI commentary (optional): Anthropic Claude
Used only to explain backtests, cycles and risk alerts. It can never trade or change settings.

1. Create an API key at console.anthropic.com → API Keys.
2. Integrations → **Anthropic Claude**: paste the key into *API key*; leave *Model* empty (defaults to `claude-opus-5-5`); tick **Enabled**; **Save**; **Test** → *connected*.
3. Use it: open any finished backtest → **AI analysis**. Only aggregated metrics are sent, never credentials or account ids.

*(OpenAI works the same way, but its Model field is required.)*

### 4.4 Email / Telegram notifications
You can configure and test these, but **messages are not delivered yet** (only in-app notifications are).
- Email: SMTP host, port (587), username, password, from and to address → Save → Test (logs in to SMTP, sends nothing).
- Telegram: create a bot with @BotFather, paste the bot token and your chat id → Save → Test (calls `getMe`).

### 4.5 MetaTrader 5 (broker). Do this with a DEMO account first
Requirements: Windows, the MT5 terminal installed and **logged in**, and API + worker running **on that same
Windows machine** (the MT5 Python package talks to the local terminal).

1. Open a **demo** account with your broker and log in to it in the MT5 terminal. Enable **Algo Trading** (toolbar button).
2. Check the account is **hedging** (the News & Volatility bot refuses netting accounts).
3. Install the package: `.venv/Scripts/pip install -e ".[mt5]"`, then restart API and worker.
4. Integrations → **MetaTrader 5**: Account login, Trade server (as shown in MT5), optional terminal path, broker symbol suffix if your broker uses one (e.g. `.r`, `m`), password → tick **Enabled** → **Save** → **Test**. The result shows server, demo/REAL, and margin mode.
5. Stop both bots and close any open cycles. Then **Settings → Trading environment → Live**: the readiness checks run (broker, account, broker instrument specs, margin mode, reconciliation, stop support, no critical breakers). Type the confirmation phrase shown, which includes your account number.
6. With a **demo** account, "Live" means real broker execution with demo money. Run like this for weeks before considering a real-money account.

> The MT5 adapter is tested against a simulated terminal only. Treat the first demo weeks as its validation:
> compare every cycle in the app with the MT5 terminal's history.

## 5. Validate the strategies on real data

Synthetic backtests say nothing about real markets. Get real data and test properly:

1. In MT5: View → Symbols → Bars (or the History Center) → export EURUSD M1 (and other pairs) to CSV.
2. Backtesting → **Import dataset**: choose the file, symbol, timeframe `M1`, **source timezone = your broker's server timezone** (often `Etc/GMT-2` or `Etc/GMT-3`, check with your broker). Files failing quality checks are rejected with a report.
3. In each bot's workspace → **Backtests**: run on the imported dataset with realistic costs.
4. On a finished run, use **Validation**: out-of-sample, walk-forward, cost and slippage stress, drawdown Monte-Carlo, and baseline. A result that only works in-sample, collapses at 1.5× spread, or doesn't beat its baseline is not an edge.

## 6. Before any real money

- [ ] Trading loop at about 2 s per tick in the deployed location.
- [ ] Several weeks of paper trading per bot, with reviewed cycles, no unexplained states, and no breakers you don't understand.
- [ ] Out-of-sample, walk-forward and cost-stressed backtests on real data that you trust.
- [ ] Several weeks on an MT5 **demo** account, with app and terminal histories matching.
- [ ] The app runs on an always-on machine (VM), with the worker restarting automatically and the clock NTP-synced.
- [ ] Health (`/api/v1/health`) monitored; you get told when the worker or broker goes down.
- [ ] Risk limits reviewed; start with the smallest sizes your broker allows.
