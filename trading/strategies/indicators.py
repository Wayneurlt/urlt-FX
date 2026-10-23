"""Causal indicators: value[i] depends only on inputs[0..i].

All functions return numpy arrays aligned with their input, with NaN where
there is not yet enough history.
"""

from __future__ import annotations

import numpy as np


def sma(x: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if period <= 0 or len(x) < period:
        return out
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[period - 1 :] = (c[period:] - c[:-period]) / period
    return out


def ema(x: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if period <= 0 or len(x) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = x[:period].mean()
    for i in range(period, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    high, low, close = (np.asarray(a, dtype=float) for a in (high, low, close))
    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0] if len(high) else np.nan
    return tr


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder's ATR (seeded with an SMA of the first ``period`` true ranges)."""
    tr = true_range(high, low, close)
    out = np.full_like(tr, np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    close = np.asarray(close, dtype=float)
    out = np.full_like(close, np.nan)
    if len(close) <= period:
        return out
    d = np.diff(close)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag = gain[:period].mean()
    al = loss[:period].mean()
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(close)):
        ag = (ag * (period - 1) + gain[i - 1]) / period
        al = (al * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def rolling_max(x: np.ndarray, window: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    for i in range(window - 1, len(x)):
        out[i] = x[i - window + 1 : i + 1].max()
    return out


def rolling_min(x: np.ndarray, window: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    for i in range(window - 1, len(x)):
        out[i] = x[i - window + 1 : i + 1].min()
    return out
