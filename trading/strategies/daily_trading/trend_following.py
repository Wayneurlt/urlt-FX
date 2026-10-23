"""Trend-following baseline (EXPERIMENTAL - not a proven profitable system).

Rules, evaluated on the last CLOSED candle ``i`` only:

LONG  when close[i] > EMA_fast[i] > EMA_slow[i]
      and RSI[i-1] <= T and RSI[i] > T             (momentum confirmation)
SHORT when close[i] < EMA_fast[i] < EMA_slow[i]
      and RSI[i-1] >= 100-T and RSI[i] < 100-T

stop   = clamp(ATR[i] * stop_atr_mult, min_stop_pips, max_stop_pips)
target = ATR[i] * target_atr_mult

If no rule matches, no signal is produced and the bot does nothing.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import Side, TriggerType
from trading.market_data.models import Candle, timeframe_delta
from trading.strategies.base import DailyStrategy, SignalEvaluation, StrategySignal
from trading.strategies.daily_trading.config import TrendFollowingParams
from trading.strategies.indicators import atr, ema, rsi

VERSION = "1.0.0"


class TrendFollowingBaseline(DailyStrategy):
    name = "trend_following_baseline"
    version = VERSION

    def __init__(self, params: TrendFollowingParams, timeframe: str, bot_id: str = "") -> None:
        self.p = params
        self.timeframe = timeframe
        self.bot_id = bot_id

    def required_bars(self) -> int:
        return max(self.p.ema_slow, self.p.rsi_period + 2, self.p.atr_period + 1) + 1

    def evaluate(
        self, symbol: str, spec: InstrumentSpec, candles: list[Candle], now: datetime
    ) -> SignalEvaluation:
        tf = timeframe_delta(self.timeframe)
        closed = [c for c in candles if c.time + tf <= now]  # never use an unfinished candle
        if len(closed) < self.required_bars():
            return SignalEvaluation(None, f"insufficient history ({len(closed)}/{self.required_bars()} bars)")
        cl = np.array([c.close for c in closed])
        hi = np.array([c.high for c in closed])
        lo = np.array([c.low for c in closed])
        ef, es = ema(cl, self.p.ema_fast), ema(cl, self.p.ema_slow)
        r = rsi(cl, self.p.rsi_period)
        a = atr(hi, lo, cl, self.p.atr_period)
        i = len(cl) - 1
        ind = {
            "close": float(cl[i]),
            "ema_fast": round(float(ef[i]), 6),
            "ema_slow": round(float(es[i]), 6),
            "rsi": round(float(r[i]), 2),
            "rsi_prev": round(float(r[i - 1]), 2),
            "atr": float(a[i]),
            "candle_time": closed[-1].time.isoformat(),
        }
        if np.isnan([ef[i], es[i], r[i], r[i - 1], a[i]]).any():
            return SignalEvaluation(None, "indicators not ready", ind)
        t = self.p.rsi_threshold
        side: Side | None = None
        if cl[i] > ef[i] > es[i] and r[i - 1] <= t < r[i]:
            side = Side.BUY
        elif cl[i] < ef[i] < es[i] and r[i - 1] >= 100 - t > r[i]:
            side = Side.SELL
        if side is None:
            trend = "up" if ef[i] > es[i] else "down"
            return SignalEvaluation(None, f"no setup (trend {trend}, RSI {r[i]:.1f})", ind)
        stop = float(a[i]) * self.p.stop_atr_mult
        stop = min(
            max(stop, spec.pips_to_price(self.p.min_stop_pips)), spec.pips_to_price(self.p.max_stop_pips)
        )
        target = float(a[i]) * self.p.target_atr_mult
        sig = StrategySignal(
            symbol=symbol,
            trigger=TriggerType.DAILY_SIGNAL,
            side=side,
            stop_distance=stop,
            target_distance=target,
            candle_time=closed[-1].time,
            dedupe_key=f"daily:{self.bot_id}:{symbol}:{closed[-1].time.isoformat()}",
            details=ind,
        )
        return SignalEvaluation(sig, f"{side.value} setup", ind)


STRATEGIES: dict[str, type[DailyStrategy]] = {TrendFollowingBaseline.name: TrendFollowingBaseline}


def build_daily_strategy(
    name: str, params: TrendFollowingParams, timeframe: str, bot_id: str = ""
) -> DailyStrategy:
    try:
        cls = STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown daily strategy {name!r}; available: {sorted(STRATEGIES)}") from exc
    return cls(params, timeframe, bot_id)  # type: ignore[call-arg]
