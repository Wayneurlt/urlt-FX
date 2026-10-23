"""Event-driven bar backtester for both bots.

Execution assumptions (all also listed in ``BacktestResult.assumptions``):

* Data are BID candles with a spread column; ASK = BID + spread.
* Signals use CLOSED bars only. A signal on bar t is filled at the OPEN of
  bar t+1+latency_bars. News entries fill at the open of the first bar whose
  open time is inside the event window.
* Market BUY fills at ask + slippage, SELL at bid - slippage. In a dual-entry
  cycle the second leg receives extra adverse slippage (latency between the
  two orders); legs are never perfectly synchronised.
* Long stops trigger on BID, short stops on ASK. If the bar OPENS beyond the
  stop (gap) the fill is the open price, not the stop. Stop fills receive
  adverse slippage.
* Intrabar ambiguity is resolved conservatively: if a bar's range contains
  both the stop and the target, the stop is assumed to be hit first. If it
  contains BOTH dual-entry stops, both legs are assumed stopped (worst case).
* Trailing stops are updated from the bar's extreme AFTER checking the stop
  on that bar, so a new stop level only applies from the next bar. With tick
  data (O=H=L=C) this is exact.
* Commission per lot per side; swap charged per lot when a position is held
  through 22:00 UTC.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from trading.backtesting.metrics import compute_metrics, sample_warnings
from trading.backtesting.models import BacktestConfig
from trading.instruments.conversion import ConversionUnavailable, RateBook
from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import BotKind, Side, TriggerType
from trading.market_data.models import TIMEFRAME_MINUTES
from trading.news.eligibility import window_for
from trading.news.models import EconomicEvent, EventStatus
from trading.risk.sizing import LegPlan, size_position
from trading.strategies.daily_trading.config import DailyTradingConfig
from trading.strategies.daily_trading.trend_following import TrendFollowingBaseline
from trading.strategies.indicators import atr, ema, rsi
from trading.strategies.news_volatility.config import NewsVolatilityConfig
from trading.strategies.news_volatility.volatility import volatility_trigger_series


class BacktestError(ValueError):
    pass


@dataclass
class _Leg:
    cycle_id: str
    side: Side
    volume: float
    entry_price: float
    expected_price: float
    entry_time: datetime
    sl: float
    tp: float | None
    initial_sl: float
    trigger: str
    commission: float = 0.0
    swap: float = 0.0
    trail_dist: float | None = None
    water: float | None = None
    trail_activate_price: float | None = None  # daily bot: start trailing past this price
    be_price: float | None = None  # daily bot: move to break-even past this price
    be_target: float | None = None
    stop_moves: int = 0


@dataclass
class _Cycle:
    id: str
    trigger: str
    event_id: str | None
    start_time: datetime
    legs: list[_Leg] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    trailing_at: datetime | None = None
    planned_risk: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class BacktestResult:
    config: dict[str, Any]
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    cycles: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    daily_equity: list[dict[str, Any]]
    warnings: list[str]
    assumptions: list[str]
    counters: dict[str, int]
    data_fingerprint: str
    bars: int

    def summary(self) -> dict[str, Any]:
        return {k: v for k, v in self.metrics.items() if not isinstance(v, dict)}


ASSUMPTIONS = [
    "Prices are BID candles; ASK = BID + spread (data spread or fixed model, times the spread multiplier).",
    "Signals use closed bars only; fills happen at the next bar open (+ configured latency bars).",
    "BUY fills at ask + slippage; SELL at bid - slippage. Dual-entry second leg gets extra slippage (orders are not simultaneous).",
    "Long stops trigger on bid, short stops on ask; gaps through a stop fill at the bar open, not at the stop.",
    "If a bar contains both stop and target, the stop is assumed hit first (conservative).",
    "If a bar contains both dual-entry stops, both legs are assumed stopped (worst case, no trailing benefit).",
    "Trailing-stop levels computed from a bar apply from the next bar; candle data cannot resolve intrabar order - prefer tick data.",
    "Take-profit fills at the target price without slippage.",
    "Commission is charged per lot per side; swap is charged per lot for each 22:00 UTC rollover held.",
    "Rejections and partial fills are drawn from a seeded pseudo-random stream (reproducible).",
]


class Backtester:
    def __init__(
        self,
        cfg: BacktestConfig,
        data: pd.DataFrame,
        spec: InstrumentSpec,
        events: list[EconomicEvent] | None = None,
        progress=None,
    ) -> None:
        self.cfg = cfg
        self.spec = spec
        self.events = events or []
        self.progress = progress
        df = data[
            (data["time"] >= pd.Timestamp(cfg.start)) & (data["time"] < pd.Timestamp(cfg.end))
        ].reset_index(drop=True)
        if len(df) < 50:
            raise BacktestError(f"not enough data in the selected period ({len(df)} bars)")
        self.df = df
        self.t: list[datetime] = [ts.to_pydatetime() for ts in df["time"]]
        self.o = df["open"].to_numpy(float).copy()
        self.h = df["high"].to_numpy(float).copy()
        self.lo = df["low"].to_numpy(float).copy()
        self.c = df["close"].to_numpy(float).copy()
        c = cfg.costs
        if c.spread_mode == "fixed" or "spread" not in df.columns:
            sp = np.full(len(df), c.fixed_spread_pips * spec.pip_size)
        else:
            sp = df["spread"].fillna(0).to_numpy(float)
            sp = np.maximum(sp, c.min_spread_pips * spec.pip_size)
        self.s = sp * c.spread_multiplier
        self.tf = timedelta(minutes=TIMEFRAME_MINUTES[cfg.base_timeframe])
        self.balance = cfg.initial_balance
        self.rng_n = 0
        self.counters: dict[str, int] = {
            k: 0
            for k in (
                "signals",
                "entries",
                "rejected_orders",
                "partial_fills",
                "unmatched_legs",
                "skipped_risk",
                "skipped_margin",
                "skipped_daily_loss",
                "skipped_consecutive_losses",
                "skipped_data_gap",
                "skipped_cooldown",
                "skipped_max_trades",
                "skipped_session",
                "skipped_spread",
                "gap_stop_fills",
                "ambiguous_bars",
                "both_legs_same_bar",
            )
        }
        self.trades: list[dict] = []
        self.cycles: list[dict] = []
        self.equity_points: list[tuple[datetime, float]] = []
        self.daily_equity: list[tuple[datetime, float]] = []
        self.warnings: list[str] = []
        self.rates_assumed = False

    # --- utilities -----------------------------------------------------------
    def _rand(self) -> float:
        self.rng_n += 1
        h = hashlib.blake2b(f"{self.cfg.seed}:{self.rng_n}".encode(), digest_size=8).digest()
        return int.from_bytes(h, "big") / 2**64

    def _slip(self, extra: float = 0.0) -> float:
        c = self.cfg.costs
        pts = c.slippage_points + extra + math.floor(self._rand() * (c.random_slippage_points + 1))
        return pts * self.spec.tick_size

    def _rate(self, from_ccy: str, price: float) -> float:
        acct = self.cfg.account_currency
        if from_ccy == acct:
            return 1.0
        # Derive from the traded pair when possible.
        if from_ccy == self.spec.quote_currency and self.spec.base_currency == acct:
            return 1.0 / price
        if from_ccy == self.spec.base_currency and self.spec.quote_currency == acct:
            return price
        try:
            self.rates_assumed = True
            return RateBook(self.cfg.conversion_rates).rate(from_ccy, acct)
        except ConversionUnavailable as exc:
            raise BacktestError(
                f"cannot convert {from_ccy} to {acct}: provide conversion_rates (e.g. {{'{from_ccy}{acct}': ...}})"
            ) from exc

    def _ratebook(self, price: float) -> RateBook:
        rb = RateBook(self.cfg.conversion_rates)
        rb.set(self.spec.symbol, price)
        return rb

    def _equity(self, i: int, open_legs: list[_Leg]) -> float:
        fl = 0.0
        for lg in open_legs:
            px = self.c[i] if lg.side is Side.BUY else self.c[i] + self.s[i]
            fl += (px - lg.entry_price) * lg.side.sign * self.spec.contract_size * lg.volume * self._rate(
                self.spec.quote_currency, px
            ) + lg.swap
        return self.balance + fl

    def _gap_before(self, i: int) -> bool:
        if i == 0:
            return False
        dt = self.t[i] - self.t[i - 1]
        if dt <= self.tf * self.cfg.execution.max_gap_bars:
            return False
        a, b = self.t[i - 1], self.t[i]
        weekend = a.weekday() == 4 and b.weekday() in (6, 0) and dt <= timedelta(days=3)
        return not weekend

    # --- fills ---------------------------------------------------------------
    def _open_leg(
        self,
        cyc: _Cycle,
        i: int,
        side: Side,
        volume: float,
        sl: float,
        tp: float | None,
        extra_slip: float = 0.0,
    ) -> _Leg | None:
        ex = self.cfg.execution
        if ex.reject_probability and self._rand() < ex.reject_probability:
            self.counters["rejected_orders"] += 1
            cyc.notes.append(f"{side.value} order rejected (simulated)")
            return None
        vol = volume
        if ex.partial_fill_probability and self._rand() < ex.partial_fill_probability:
            vol = self.spec.normalize_volume(volume * ex.partial_fill_ratio)
            self.counters["partial_fills"] += 1
            if vol <= 0:
                return None
        bid = self.o[i]
        ask = bid + self.s[i]
        slip = self._slip(extra_slip)
        price = self.spec.round_price(ask + slip) if side is Side.BUY else self.spec.round_price(bid - slip)
        expected = ask if side is Side.BUY else bid
        comm = -self.cfg.costs.commission_per_lot_side * vol
        self.balance += comm
        return _Leg(cyc.id, side, vol, price, expected, self.t[i], sl, tp, sl, cyc.trigger, commission=comm)

    def _close_leg(
        self, cyc: _Cycle, lg: _Leg, i: int, price: float, reason: str, when: datetime | None = None
    ) -> dict:
        px = self.spec.round_price(price)
        gross = (
            (px - lg.entry_price)
            * lg.side.sign
            * self.spec.contract_size
            * lg.volume
            * self._rate(self.spec.quote_currency, px)
        )
        comm = -self.cfg.costs.commission_per_lot_side * lg.volume
        self.balance += gross + comm + lg.swap
        lg.commission += comm
        rec = {
            "cycle_id": cyc.id,
            "symbol": self.spec.symbol,
            "side": lg.side.value,
            "volume": lg.volume,
            "trigger": cyc.trigger,
            "event_id": cyc.event_id,
            "entry_time": lg.entry_time,
            "entry_price": lg.entry_price,
            "expected_price": lg.expected_price,
            "initial_sl": lg.initial_sl,
            "final_sl": lg.sl,
            "tp": lg.tp,
            "exit_time": when or self.t[i],
            "exit_price": px,
            "exit_reason": reason,
            "gross_pnl": round(gross, 2),
            "commission": round(lg.commission, 2),
            "swap": round(lg.swap, 2),
            "net_pnl": round(gross + lg.commission + lg.swap, 2),
            "stop_moves": lg.stop_moves,
            "slippage_pips": round(
                self.spec.price_to_pips((lg.entry_price - lg.expected_price) * lg.side.sign), 2
            ),
            "strategy_version": self.cfg.strategy_version,
        }
        self.trades.append(rec)
        cyc.closed.append(rec)
        self.equity_points.append((when or self.t[i], self.balance))
        return rec

    def _check_exit(self, lg: _Leg, i: int) -> tuple[float, str] | None:
        """Stop/target check for one leg on bar i. Stop first (conservative)."""
        o, h, lo, s = self.o[i], self.h[i], self.lo[i], self.s[i]
        if lg.side is Side.BUY:
            if o <= lg.sl:
                self.counters["gap_stop_fills"] += 1
                return o - self._slip(), "gap_stop"
            if lo <= lg.sl:
                if lg.tp is not None and h >= lg.tp:
                    self.counters["ambiguous_bars"] += 1
                return lg.sl - self._slip(), "stop"
            if lg.tp is not None and h >= lg.tp:
                return lg.tp, "take_profit"
        else:
            ao, ah, al = o + s, h + s, lo + s
            if ao >= lg.sl:
                self.counters["gap_stop_fills"] += 1
                return ao + self._slip(), "gap_stop"
            if ah >= lg.sl:
                if lg.tp is not None and al <= lg.tp:
                    self.counters["ambiguous_bars"] += 1
                return lg.sl + self._slip(), "stop"
            if lg.tp is not None and al <= lg.tp:
                return lg.tp, "take_profit"
        return None

    def _apply_swap(self, legs: list[_Leg], prev: datetime, now: datetime) -> None:
        d0 = (prev - timedelta(hours=22)).date()
        d1 = (now - timedelta(hours=22)).date()
        n = (d1 - d0).days
        if n <= 0:
            return
        for lg in legs:
            rate = (
                self.cfg.costs.swap_long_per_lot_day
                if lg.side is Side.BUY
                else self.cfg.costs.swap_short_per_lot_day
            )
            lg.swap += rate * lg.volume * n

    def _finish_cycle(self, cyc: _Cycle, outcome: str) -> None:
        net = round(sum(t["net_pnl"] for t in cyc.closed), 2)
        self.cycles.append(
            {
                "id": cyc.id,
                "trigger": cyc.trigger,
                "event_id": cyc.event_id,
                "start_time": cyc.start_time,
                "end_time": max((t["exit_time"] for t in cyc.closed), default=cyc.start_time),
                "legs": len(cyc.closed),
                "net_pnl": net,
                "outcome": outcome,
                "trailing_activated_at": cyc.trailing_at,
                "planned_risk": round(cyc.planned_risk, 2),
                "notes": cyc.notes,
            }
        )

    # --- signal precomputation ----------------------------------------------
    def _news_vol_entries(self, nv: NewsVolatilityConfig) -> dict[int, tuple[str, str | None]]:
        entries: dict[int, tuple[str, str | None]] = {}
        lat = self.cfg.execution.latency_bars
        if nv.entry_mode.volatility:
            vc = nv.volatility
            if vc.timeframe != self.cfg.base_timeframe:
                raise BacktestError(
                    f"volatility timeframe {vc.timeframe} must equal the base timeframe {self.cfg.base_timeframe} in backtests"
                )
            trig = volatility_trigger_series(self.h, self.lo, self.c, self.spec, vc)
            last_t: datetime | None = None
            for i in np.where(trig)[0]:
                j = int(i) + 1 + lat
                if j >= len(self.t):
                    continue
                if last_t is not None and self.t[j] - last_t < timedelta(minutes=vc.cooldown_minutes):
                    continue
                entries[j] = (TriggerType.VOLATILITY.value, None)
                last_t = self.t[j]
        if nv.entry_mode.news:
            for ev in self.events:
                if ev.status in (EventStatus.CANCELLED, EventStatus.POSTPONED):
                    continue
                if ev.currency not in nv.news.currencies or ev.impact.value not in nv.news.impact_levels:
                    continue
                if ev.currency not in (self.spec.base_currency, self.spec.quote_currency):
                    continue
                ws, we = window_for(ev, nv.news)
                idx = int(self.df["time"].searchsorted(pd.Timestamp(ws.astimezone(UTC)), side="left"))
                if idx < len(self.t) and self.t[idx] <= we:
                    entries[idx] = (
                        TriggerType.NEWS.value,
                        ev.event_id,
                    )  # news takes precedence on the same bar
        self.counters["signals"] = len(entries)
        return entries

    def _daily_entries(self, dc: DailyTradingConfig) -> dict[int, dict]:
        """Signals on the strategy timeframe, mapped to base-bar fill indices."""
        tf_min = TIMEFRAME_MINUTES[dc.timeframe]
        base_min = TIMEFRAME_MINUTES[self.cfg.base_timeframe]
        if tf_min < base_min:
            raise BacktestError("strategy timeframe must be >= base timeframe")
        if tf_min == base_min:
            bars = self.df[["time", "open", "high", "low", "close"]].copy()
        else:
            bars = (
                self.df.set_index("time")[["open", "high", "low", "close"]]
                .resample(f"{tf_min}min", label="left", closed="left", origin="epoch")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                .dropna()
                .reset_index()
            )
            # Drop the final bar if the base data ends before it completes.
            last_needed = (
                bars["time"].iloc[-1] + pd.Timedelta(minutes=tf_min) - pd.Timedelta(minutes=base_min)
            )
            if self.df["time"].iloc[-1] < last_needed:
                bars = bars.iloc[:-1]
        strat = TrendFollowingBaseline(dc.params, dc.timeframe)
        p = dc.params
        cl, hi, lo = bars["close"].to_numpy(float), bars["high"].to_numpy(float), bars["low"].to_numpy(float)
        ef, es, r, a = (
            ema(cl, p.ema_fast),
            ema(cl, p.ema_slow),
            rsi(cl, p.rsi_period),
            atr(hi, lo, cl, p.atr_period),
        )
        t = p.rsi_threshold
        base_times = self.df["time"]
        out: dict[int, dict] = {}
        for i in range(strat.required_bars() - 1, len(cl)):
            if np.isnan([ef[i], es[i], r[i], r[i - 1], a[i]]).any():
                continue
            side = None
            if cl[i] > ef[i] > es[i] and r[i - 1] <= t < r[i]:
                side = Side.BUY
            elif cl[i] < ef[i] < es[i] and r[i - 1] >= 100 - t > r[i]:
                side = Side.SELL
            if side is None:
                continue
            close_time = bars["time"].iloc[i] + pd.Timedelta(minutes=tf_min)
            j = int(base_times.searchsorted(close_time, side="left")) + self.cfg.execution.latency_bars
            if j >= len(self.t):
                continue
            stop = min(
                max(a[i] * p.stop_atr_mult, self.spec.pips_to_price(p.min_stop_pips)),
                self.spec.pips_to_price(p.max_stop_pips),
            )
            out[j] = {
                "side": side,
                "stop": float(stop),
                "target": float(a[i] * p.target_atr_mult),
                "atr": float(a[i]),
                "signal_time": bars["time"].iloc[i].to_pydatetime(),
            }
        self.counters["signals"] = len(out)
        return out

    # --- main loop -------------------------------------------------------------
    def run(self) -> BacktestResult:
        try:
            return self._run()
        except ConversionUnavailable as exc:
            raise BacktestError(f"{exc}: provide conversion_rates for the account currency") from exc

    def _run(self) -> BacktestResult:
        kind = self.cfg.bot_kind
        if kind is BotKind.NEWS_VOLATILITY:
            nv = NewsVolatilityConfig(**self.cfg.strategy)
            if self.cfg.baseline_mode == "no_trailing":
                nv.trailing.enabled = False
            entries: dict[int, Any] = self._news_vol_entries(nv)
            dc = None
        else:
            dc = DailyTradingConfig(**self.cfg.strategy)
            entries = self._daily_entries(dc)
            nv = None

        active: list[_Cycle] = []
        day = None
        day_start_eq = self.balance
        consec_losses = 0
        halted_today = False
        trades_today = 0
        last_entry_time: datetime | None = None
        n = len(self.t)
        cyc_seq = 0
        report_every = max(1, n // 50)

        for i in range(n):
            if self.progress and i % report_every == 0:
                self.progress(i / n)
            now = self.t[i]
            open_legs = [lg for c in active for lg in c.legs]
            if i > 0:
                self._apply_swap(open_legs, self.t[i - 1], now)
            d = now.date()
            if d != day:
                if day is not None:
                    self.daily_equity.append((self.t[i - 1], self._equity(i - 1, open_legs)))
                day = d
                day_start_eq = self._equity(i, open_legs) if i > 0 else self.balance
                halted_today = False
                trades_today = 0
                if consec_losses >= self.cfg.risk.max_consecutive_losses:
                    consec_losses = 0  # the consecutive-loss pause lasts until the next daily reset
            gap = self._gap_before(i)
            if gap:
                self.counters["skipped_data_gap"] += 1 if i in entries else 0

            # 1. manage open cycles on this bar
            for cyc in list(active):
                if nv is not None:
                    self._manage_dual(cyc, i, nv)
                else:
                    self._manage_single(cyc, i, dc)  # type: ignore[arg-type]
                if not cyc.legs:
                    active.remove(cyc)
                    net = sum(t["net_pnl"] for t in cyc.closed)
                    consec_losses = consec_losses + 1 if net <= 0 else 0
                    if not any(c["id"] == cyc.id for c in self.cycles):
                        self._finish_cycle(cyc, self._outcome(cyc))

            # 2. daily-loss halt check on bar close equity
            eq = self._equity(i, [lg for c in active for lg in c.legs])
            if (
                not halted_today
                and day_start_eq - eq >= day_start_eq * self.cfg.risk.max_daily_loss_pct / 100
            ):
                halted_today = True

            # 3. entries at the open of bar i
            if i not in entries:
                continue
            if gap:
                continue
            if halted_today:
                self.counters["skipped_daily_loss"] += 1
                continue
            if consec_losses >= self.cfg.risk.max_consecutive_losses:
                self.counters["skipped_consecutive_losses"] += 1
                continue
            if active:
                self.counters["skipped_risk"] += 1  # one active cycle per instrument
                continue
            spread_pips = self.spec.price_to_pips(self.s[i])
            if nv is not None:
                trig, event_id = entries[i]
                if spread_pips > self.cfg.risk.max_spread_pips:
                    self.counters["skipped_spread"] += 1
                    continue
                cyc_seq += 1
                cyc = _Cycle(f"bt-{cyc_seq}", trig, event_id, now)
                if self._enter_dual(cyc, i, nv):
                    active.append(cyc)
            else:
                assert dc is not None
                sig = entries[i]
                from trading.strategies.daily_trading.trade import in_entry_session

                ok, _ = in_entry_session(now, dc)
                if not ok:
                    self.counters["skipped_session"] += 1
                    continue
                if trades_today >= dc.max_trades_per_day:
                    self.counters["skipped_max_trades"] += 1
                    continue
                if last_entry_time and now - last_entry_time < timedelta(minutes=dc.cooldown_minutes):
                    self.counters["skipped_cooldown"] += 1
                    continue
                cyc_seq += 1
                cyc = _Cycle(f"bt-{cyc_seq}", TriggerType.DAILY_SIGNAL.value, None, now)
                if self._enter_single(cyc, i, sig, dc):
                    active.append(cyc)
                    trades_today += 1
                    last_entry_time = now

        # close anything still open at the end of data
        last = n - 1
        for cyc in active:
            for lg in list(cyc.legs):
                px = self.c[last] if lg.side is Side.BUY else self.c[last] + self.s[last]
                self._close_leg(cyc, lg, last, px, "end_of_data", self.t[last] + self.tf)
                cyc.legs.remove(lg)
            self._finish_cycle(cyc, "end_of_data")
        self.daily_equity.append((self.t[last], self.balance))
        if self.progress:
            self.progress(1.0)
        return self._result()

    # --- dual entry ----------------------------------------------------------
    def _enter_dual(self, cyc: _Cycle, i: int, nv: NewsVolatilityConfig) -> bool:
        bid, ask = self.o[i], self.o[i] + self.s[i]
        d = self.spec.pips_to_price(nv.stop_pips(self.spec.symbol))
        buy_sl = self.spec.round_price_down(ask - d)
        sell_sl = self.spec.round_price_up(bid + d)
        plans = [LegPlan(Side.BUY, ask, buy_sl), LegPlan(Side.SELL, bid, sell_sl)]
        eq = self._equity(i, [])
        sizing = size_position(
            self.spec,
            plans,
            eq,
            self.cfg.risk.risk_per_entry_pct,
            self.cfg.account_currency,
            self._ratebook(bid),
            self.cfg.costs.commission_per_lot_side,
            self.cfg.costs.slippage_points + self.cfg.costs.random_slippage_points / 2,
        )
        if sizing.volume <= 0:
            self.counters["skipped_risk"] += 1
            return False
        margin = (
            2
            * sizing.volume
            * self.spec.contract_size
            * self._rate(self.spec.base_currency, bid)
            / self.cfg.execution.leverage
        )
        if margin > eq * 0.5:
            self.counters["skipped_margin"] += 1
            return False
        cyc.planned_risk = sizing.planned_risk
        # BUY first, SELL second (second leg gets latency slippage)
        b = self._open_leg(cyc, i, Side.BUY, sizing.volume, buy_sl, None)
        s = self._open_leg(
            cyc, i, Side.SELL, sizing.volume, sell_sl, None, self.cfg.costs.second_leg_extra_slippage_points
        )
        legs = [lg for lg in (b, s) if lg is not None]
        if not legs:
            self.cycles.append(
                {
                    "id": cyc.id,
                    "trigger": cyc.trigger,
                    "event_id": cyc.event_id,
                    "start_time": cyc.start_time,
                    "end_time": cyc.start_time,
                    "legs": 0,
                    "net_pnl": 0.0,
                    "outcome": "no_fill",
                    "trailing_activated_at": None,
                    "planned_risk": 0.0,
                    "notes": cyc.notes,
                }
            )
            return False
        self.counters["entries"] += 1
        cyc.legs = legs
        if len(legs) == 1:
            # Unmatched exposure: default policy closes it at the next executable price.
            self.counters["unmatched_legs"] += 1
            lg = legs[0]
            j = min(i + 1, len(self.t) - 1)
            px = self.o[j] - self._slip() if lg.side is Side.BUY else self.o[j] + self.s[j] + self._slip()
            self._close_leg(cyc, lg, j, px, "unmatched_exposure")
            cyc.legs = []
            self._finish_cycle(cyc, "unmatched_closed")
            return False
        # Both legs could stop on the entry bar itself
        self._manage_dual(cyc, i, nv, entry_bar=True)
        if not cyc.legs:
            self._finish_cycle(cyc, self._outcome(cyc))
            return False
        return True

    def _manage_dual(self, cyc: _Cycle, i: int, nv: NewsVolatilityConfig, entry_bar: bool = False) -> None:
        if not cyc.legs:
            return
        if not entry_bar and self.t[i] - cyc.start_time > timedelta(minutes=nv.max_cycle_duration_minutes):
            for lg in list(cyc.legs):
                px = self.o[i] - self._slip() if lg.side is Side.BUY else self.o[i] + self.s[i] + self._slip()
                self._close_leg(cyc, lg, i, px, "max_duration")
                cyc.legs.remove(lg)
            return
        hits = [(lg, self._check_exit(lg, i)) for lg in cyc.legs]
        hit = [(lg, x) for lg, x in hits if x is not None]
        if len(cyc.legs) == 2 and len(hit) == 2:
            self.counters["both_legs_same_bar"] += 1
        trailing_before = cyc.trailing_at is not None
        for lg, (px, why) in hit:
            reason = (
                "trailing_stop"
                if trailing_before and lg.trail_dist is not None
                else ("stop_loss" if why in ("stop", "gap_stop") else why)
            )
            self._close_leg(cyc, lg, i, px, reason)
            cyc.legs.remove(lg)
        # Activate trailing on the survivor after a confirmed stop-out (from the next bar).
        if len(cyc.legs) == 1 and not trailing_before and hit and nv.trailing.enabled:
            sv = cyc.legs[0]
            sv.trail_dist = self.spec.pips_to_price(nv.trailing_pips(self.spec.symbol))
            sv.water = self.c[i] if sv.side is Side.BUY else self.c[i] + self.s[i]
            cyc.trailing_at = self.t[i]
            self._trail_update(sv)
        elif cyc.legs and trailing_before:
            sv = cyc.legs[0]
            if sv.side is Side.BUY:
                sv.water = max(sv.water or self.h[i], self.h[i])
            else:
                sv.water = min(sv.water or (self.lo[i] + self.s[i]), self.lo[i] + self.s[i])
            self._trail_update(sv)

    def _trail_update(self, lg: _Leg) -> None:
        if lg.trail_dist is None or lg.water is None:
            return
        if lg.side is Side.BUY:
            cand = self.spec.round_price_down(lg.water - lg.trail_dist)
            if cand > lg.sl + self.spec.tick_size / 2:
                lg.sl = cand
                lg.stop_moves += 1
        else:
            cand = self.spec.round_price_up(lg.water + lg.trail_dist)
            if cand < lg.sl - self.spec.tick_size / 2:
                lg.sl = cand
                lg.stop_moves += 1

    def _outcome(self, cyc: _Cycle) -> str:
        reasons = [t["exit_reason"] for t in cyc.closed]
        if self.cfg.bot_kind is BotKind.DAILY_TRADING:
            return reasons[-1] if reasons else "closed"
        if cyc.trailing_at is None and reasons.count("stop_loss") == 2:
            return "both_stopped"
        if cyc.trailing_at is not None:
            return "trailed"
        return reasons[-1] if reasons else "closed"

    # --- single (daily) --------------------------------------------------------
    def _enter_single(self, cyc: _Cycle, i: int, sig: dict, dc: DailyTradingConfig) -> bool:
        side: Side = sig["side"]
        if self.cfg.baseline_mode == "random_direction":
            side = Side.BUY if self._rand() < 0.5 else Side.SELL
        bid, ask = self.o[i], self.o[i] + self.s[i]
        entry = ask if side is Side.BUY else bid
        sl = (
            self.spec.round_price_down(entry - sig["stop"])
            if side is Side.BUY
            else self.spec.round_price_up(entry + sig["stop"])
        )
        tp = self.spec.round_price(entry + sig["target"] * side.sign)
        eq = self._equity(i, [])
        sizing = size_position(
            self.spec,
            [LegPlan(side, entry, sl)],
            eq,
            self.cfg.risk.risk_per_entry_pct,
            self.cfg.account_currency,
            self._ratebook(bid),
            self.cfg.costs.commission_per_lot_side,
            self.cfg.costs.slippage_points + self.cfg.costs.random_slippage_points / 2,
        )
        if sizing.volume <= 0:
            self.counters["skipped_risk"] += 1
            return False
        margin = (
            sizing.volume
            * self.spec.contract_size
            * self._rate(self.spec.base_currency, bid)
            / self.cfg.execution.leverage
        )
        if margin > eq * 0.5:
            self.counters["skipped_margin"] += 1
            return False
        lg = self._open_leg(cyc, i, side, sizing.volume, sl, tp)
        if lg is None:
            self.cycles.append(
                {
                    "id": cyc.id,
                    "trigger": cyc.trigger,
                    "event_id": None,
                    "start_time": cyc.start_time,
                    "end_time": cyc.start_time,
                    "legs": 0,
                    "net_pnl": 0.0,
                    "outcome": "no_fill",
                    "trailing_activated_at": None,
                    "planned_risk": 0.0,
                    "notes": cyc.notes,
                }
            )
            return False
        r = abs(entry - sl)
        if dc.break_even.enabled:
            lg.be_price = entry + side.sign * r * dc.break_even.trigger_r
            lg.be_target = self.spec.round_price(
                lg.entry_price + side.sign * self.spec.pips_to_price(dc.break_even.offset_pips)
            )
        if dc.trailing.enabled:
            lg.trail_activate_price = entry + side.sign * r * dc.trailing.activate_r
            lg.trail_dist = self.spec.round_price(sig["atr"] * dc.trailing.distance_atr_mult)
        cyc.planned_risk = sizing.planned_risk
        cyc.legs = [lg]
        self.counters["entries"] += 1
        self._manage_single(cyc, i, dc)
        if not cyc.legs:
            self._finish_cycle(cyc, self._outcome(cyc))
            return False
        return True

    def _manage_single(self, cyc: _Cycle, i: int, dc: DailyTradingConfig) -> None:
        if not cyc.legs:
            return
        lg = cyc.legs[0]
        now = self.t[i]
        from trading.strategies.daily_trading.trade import past_close_time

        if now - lg.entry_time > timedelta(minutes=dc.max_holding_minutes) or past_close_time(
            now, dc, lg.entry_time
        ):
            reason = (
                "max_duration"
                if now - lg.entry_time > timedelta(minutes=dc.max_holding_minutes)
                else "session_cutoff"
            )
            px = self.o[i] - self._slip() if lg.side is Side.BUY else self.o[i] + self.s[i] + self._slip()
            self._close_leg(cyc, lg, i, px, reason)
            cyc.legs = []
            return
        x = self._check_exit(lg, i)
        if x is not None:
            px, why = x
            reason = (
                "take_profit"
                if why == "take_profit"
                else (
                    "trailing_stop"
                    if lg.water is not None
                    else ("break_even_stop" if lg.stop_moves else "stop_loss")
                )
            )
            self._close_leg(cyc, lg, i, px, reason)
            cyc.legs = []
            return
        # Stop management from this bar's extremes, effective next bar.
        fav = self.h[i] if lg.side is Side.BUY else self.lo[i] + self.s[i]
        if lg.be_price is not None and lg.be_target is not None and (fav - lg.be_price) * lg.side.sign >= 0:
            if (lg.be_target - lg.sl) * lg.side.sign > 0:
                lg.sl = lg.be_target
                lg.stop_moves += 1
            lg.be_price = None
        if lg.trail_dist is not None:
            if (
                lg.water is None
                and lg.trail_activate_price is not None
                and (fav - lg.trail_activate_price) * lg.side.sign >= 0
            ):
                lg.water = fav
            elif lg.water is not None:
                lg.water = max(lg.water, fav) if lg.side is Side.BUY else min(lg.water, fav)
            if lg.water is not None:
                self._trail_update(lg)

    # --- result ----------------------------------------------------------------
    def _result(self) -> BacktestResult:
        start, end = self.t[0], self.t[-1] + self.tf
        eq_curve = [(start, self.cfg.initial_balance), *sorted(self.equity_points)]
        metrics = compute_metrics(
            self.trades, self.cycles, eq_curve, self.cfg.initial_balance, start, end, self.daily_equity
        )
        warnings = sample_warnings(metrics)
        c = self.cfg.costs
        if c.commission_per_lot_side == 0 and c.slippage_points == 0 and c.random_slippage_points == 0:
            warnings.append("Zero commission and zero slippage are unrealistic; results are optimistic.")
        if self.cfg.data_source == "synthetic":
            warnings.append(
                "Run on SYNTHETIC generated prices: results say nothing about real-market performance."
            )
        if self.cfg.base_timeframe != "M1":
            warnings.append(
                f"Execution resolution is {self.cfg.base_timeframe}; intrabar stop/trailing order is approximated conservatively."
            )
        else:
            warnings.append(
                "Candle data: intrabar order of highs/lows is unknown; conservative assumptions applied. Tick data gives more accurate trailing-stop results."
            )
        if self.counters["ambiguous_bars"]:
            warnings.append(
                f"{self.counters['ambiguous_bars']} bars contained both stop and target; the stop was assumed first."
            )
        if self.counters["both_legs_same_bar"]:
            warnings.append(
                f"{self.counters['both_legs_same_bar']} cycles had both stops inside one bar; both were assumed stopped."
            )
        if self.rates_assumed:
            warnings.append(
                f"Fixed conversion rates were used for P/L conversion: {self.cfg.conversion_rates}."
            )
        if self.counters["skipped_data_gap"]:
            warnings.append(f"{self.counters['skipped_data_gap']} signals skipped because of data gaps.")
        fp = hashlib.sha256(
            pd.util.hash_pandas_object(
                self.df[["time", "open", "high", "low", "close"]], index=False
            ).values.tobytes()
        ).hexdigest()[:16]
        step = max(1, len(eq_curve) // 2000)
        return BacktestResult(
            config=self.cfg.model_dump(mode="json"),
            metrics=metrics,
            trades=[_jsonable(t) for t in self.trades],
            cycles=[_jsonable(c) for c in self.cycles],
            equity_curve=[{"time": t.isoformat(), "equity": round(e, 2)} for t, e in eq_curve[::step]],
            daily_equity=[{"time": t.isoformat(), "equity": round(e, 2)} for t, e in self.daily_equity],
            warnings=warnings,
            assumptions=ASSUMPTIONS,
            counters=self.counters,
            data_fingerprint=fp,
            bars=len(self.t),
        )


def _jsonable(d: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, np.floating):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def run_backtest(
    cfg: BacktestConfig,
    data: pd.DataFrame,
    spec: InstrumentSpec,
    events: list[EconomicEvent] | None = None,
    progress=None,
) -> BacktestResult:
    return Backtester(cfg, data, spec, events, progress).run()
