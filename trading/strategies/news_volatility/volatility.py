"""Volatility-breakout detector (independent of news).

All rules are evaluated on CLOSED candles only. A trigger here is only a
candidate; the full eligibility checklist and the risk engine still apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from trading.instruments.spec import InstrumentSpec
from trading.market_data.models import Candle, timeframe_delta
from trading.strategies.indicators import atr
from trading.strategies.news_volatility.config import VolatilityConfig


@dataclass
class RuleResult:
    name: str
    passed: bool
    value: float | None
    threshold: float | None
    detail: str = ""


@dataclass
class VolatilitySignal:
    triggered: bool
    rules: list[RuleResult] = field(default_factory=list)
    candle_time: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "triggered": self.triggered,
            "candle_time": self.candle_time.isoformat() if self.candle_time else None,
            "rules": [r.__dict__ for r in self.rules],
        }


def required_bars(cfg: VolatilityConfig) -> int:
    return max(
        cfg.atr_period + cfg.atr_baseline_period + 1,
        cfg.range_lookback + 1,
        cfg.breakout_lookback + 1,
        cfg.expansion_lookback + 1,
    )


def evaluate_volatility(
    candles: list[Candle], now: datetime, spec: InstrumentSpec, cfg: VolatilityConfig
) -> VolatilitySignal:
    tf = timeframe_delta(cfg.timeframe)
    closed = [c for c in candles if c.time + tf <= now]
    rules: list[RuleResult] = []
    need = required_bars(cfg)
    if len(closed) < need:
        rules.append(RuleResult("data_sufficient", False, len(closed), need, "not enough closed candles"))
        return VolatilitySignal(False, rules)
    rules.append(RuleResult("data_sufficient", True, len(closed), need))

    last = closed[-1]
    age_bars = (now - (last.time + tf)) / tf
    rules.append(
        RuleResult(
            "candle_fresh",
            age_bars <= cfg.max_candle_age_bars,
            round(age_bars, 2),
            cfg.max_candle_age_bars,
            "latest closed candle is too old (data gap or stale feed)"
            if age_bars > cfg.max_candle_age_bars
            else "",
        )
    )

    h = np.array([c.high for c in closed])
    lo = np.array([c.low for c in closed])
    cl = np.array([c.close for c in closed])
    a = atr(h, lo, cl, cfg.atr_period)
    cur_atr = float(a[-1])
    base = a[-1 - cfg.atr_baseline_period : -1]
    base = base[~np.isnan(base)]
    base_atr = float(base.mean()) if len(base) else float("nan")
    ratio = cur_atr / base_atr if base_atr and base_atr > 0 else 0.0
    rules.append(RuleResult("atr_ratio", ratio >= cfg.atr_ratio_min, round(ratio, 3), cfg.atr_ratio_min))

    ranges = h - lo
    med = float(np.median(ranges[-1 - cfg.range_lookback : -1]))
    mult = float(ranges[-1] / med) if med > 0 else 0.0
    rules.append(
        RuleResult("range_multiple", mult >= cfg.range_multiple_min, round(mult, 3), cfg.range_multiple_min)
    )

    exp = abs(cl[-1] - cl[-1 - cfg.expansion_lookback])
    exp_pips = spec.price_to_pips(float(exp))
    rules.append(
        RuleResult(
            "price_expansion", exp_pips >= cfg.expansion_min_pips, round(exp_pips, 2), cfg.expansion_min_pips
        )
    )

    if cfg.require_breakout:
        prior_h = h[-1 - cfg.breakout_lookback : -1].max()
        prior_l = lo[-1 - cfg.breakout_lookback : -1].min()
        bo = cl[-1] > prior_h or cl[-1] < prior_l
        rules.append(
            RuleResult("breakout", bool(bo), float(cl[-1]), None, f"prior range {prior_l}-{prior_h}")
        )

    triggered = all(r.passed for r in rules)
    return VolatilitySignal(triggered, rules, last.time)


def volatility_trigger_series(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, spec: InstrumentSpec, cfg: VolatilityConfig
) -> np.ndarray:
    """Vectorised, causal equivalent of ``evaluate_volatility`` for backtests.

    ``out[i]`` is True when the detector would trigger on the close of bar i
    using bars 0..i only (the candle-freshness rule is handled by the
    backtester's data-gap logic instead).
    """
    import pandas as pd

    h, lo, cl = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(cl)
    if n == 0:
        return np.zeros(0, dtype=bool)
    a = atr(h, lo, cl, cfg.atr_period)
    base = pd.Series(a).shift(1).rolling(cfg.atr_baseline_period, min_periods=1).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(base > 0, a / base, 0.0)
        rng = h - lo
        med = pd.Series(rng).shift(1).rolling(cfg.range_lookback).median().to_numpy()
        mult = np.where(med > 0, rng / med, 0.0)
    exp = np.abs(cl - pd.Series(cl).shift(cfg.expansion_lookback).to_numpy()) / spec.pip_size
    ok = (
        (ratio >= cfg.atr_ratio_min)
        & (mult >= cfg.range_multiple_min)
        & (np.nan_to_num(exp) >= cfg.expansion_min_pips)
    )
    if cfg.require_breakout:
        ph = pd.Series(h).shift(1).rolling(cfg.breakout_lookback).max().to_numpy()
        pl = pd.Series(lo).shift(1).rolling(cfg.breakout_lookback).min().to_numpy()
        ok &= (cl > ph) | (cl < pl)
    ok[: required_bars(cfg) - 1] = False
    return np.nan_to_num(ok, nan=False).astype(bool)
