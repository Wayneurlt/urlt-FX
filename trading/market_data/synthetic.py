"""Deterministic synthetic price generator.

THIS IS NOT MARKET DATA. Every quote and candle produced here is tagged
``DataSource.SYNTHETIC`` and the UI labels it as such. It exists so the
platform can run end to end (paper simulation, demos, tests) before a real
data provider is configured.

The generator is stateless: the price at any timestamp is a pure function of
(seed, symbol, time). That lets the API process and the worker process agree
on prices without sharing memory, and makes simulations reproducible.

Model: log-price = sum of interpolated value-noise octaves (multi-scale drift
and chop) + decaying "volatility bursts" at pseudo-random times, which give
the volatility detector something real to detect.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from trading.instruments.conversion import RateBook
from trading.instruments.spec import InstrumentRegistry, InstrumentSpec
from trading.interfaces.types import DataSource
from trading.market_data.models import TIMEFRAME_MINUTES, Candle, Quote
from trading.market_data.provider import MarketStatus, fx_market_open

_MASK = np.uint64(0xFFFFFFFFFFFFFFFF)

BASE_PRICES: dict[str, float] = {
    "EURUSD": 1.0850,
    "GBPUSD": 1.2700,
    "AUDUSD": 0.6600,
    "NZDUSD": 0.6000,
    "USDJPY": 150.00,
    "USDCHF": 0.8800,
    "USDCAD": 1.3600,
    "EURJPY": 162.75,
    "GBPJPY": 190.50,
    "EURGBP": 0.8545,
}
BASE_SPREAD_PIPS: dict[str, float] = {"EURUSD": 0.8, "GBPUSD": 1.1, "USDJPY": 0.9, "EURGBP": 1.2}

# (period seconds, amplitude in log-price)
OCTAVES: tuple[tuple[int, float], ...] = (
    (86400 * 7, 0.020),
    (86400, 0.0055),
    (14400, 0.0028),
    (3600, 0.0014),
    (900, 0.0007),
    (300, 0.00035),
    (60, 0.00016),
    (10, 0.00005),
)
BURST_PROBABILITY_PER_HOUR = 0.05
BURST_DECAY_SECONDS = 5400.0
BURST_RAMP_SECONDS = 45.0


def _splitmix64(x: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        z = (x + np.uint64(0x9E3779B97F4A7C15)) & _MASK
        z = ((z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)) & _MASK
        z = ((z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)) & _MASK
        return z ^ (z >> np.uint64(31))


def _uniform(keys: np.ndarray, salt: int) -> np.ndarray:
    """Uniform [0, 1) from integer keys and an integer salt."""
    with np.errstate(over="ignore"):
        x = keys.astype(np.int64).astype(np.uint64) * np.uint64(0x2545F4914F6CDD1D) + np.uint64(
            salt & 0xFFFFFFFFFFFF
        )
    h = _splitmix64(x & _MASK)
    return (h >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def _salt(seed: int, symbol: str, tag: str) -> int:
    s = 1469598103934665603
    for ch in f"{seed}|{symbol}|{tag}":
        s = ((s ^ ord(ch)) * 1099511628211) & 0xFFFFFFFFFFFF
    return s


class SyntheticMarketData:
    source = DataSource.SYNTHETIC
    name = "synthetic"

    def __init__(self, seed: int = 7, registry: InstrumentRegistry | None = None) -> None:
        self.seed = seed
        self.registry = registry or InstrumentRegistry.with_defaults()

    # --- core path ---------------------------------------------------------
    def _log_offset(self, symbol: str, t: np.ndarray) -> np.ndarray:
        """log(price/base) at epoch seconds ``t`` (float array)."""
        out = np.zeros_like(t, dtype=np.float64)
        for period, amp in OCTAVES:
            salt = _salt(self.seed, symbol, f"oct{period}")
            k = np.floor(t / period)
            frac = (t - k * period) / period
            v0 = _uniform(k, salt) * 2 - 1
            v1 = _uniform(k + 1, salt) * 2 - 1
            w = frac * frac * (3 - 2 * frac)
            out += amp * (v0 + (v1 - v0) * w)
        out += self._burst_component(symbol, t)
        return out

    def _bursts_near(
        self, symbol: str, t: np.ndarray, lookback_hours: int = 3
    ) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """For each of the last ``lookback_hours`` hourly slots: (active, burst_time, magnitude)."""
        res = []
        hour = np.floor(t / 3600)
        s_p = _salt(self.seed, symbol, "burst_p")
        s_t = _salt(self.seed, symbol, "burst_t")
        s_m = _salt(self.seed, symbol, "burst_m")
        for back in range(lookback_hours):
            h = hour - back
            active = _uniform(h, s_p) < BURST_PROBABILITY_PER_HOUR
            bt = h * 3600 + _uniform(h, s_t) * 3600
            u = _uniform(h, s_m)
            mag = (0.0015 + 0.0035 * np.abs(u * 2 - 1)) * np.where(u < 0.5, -1.0, 1.0)
            res.append((active, bt, mag))
        return res

    def _burst_component(self, symbol: str, t: np.ndarray) -> np.ndarray:
        out = np.zeros_like(t, dtype=np.float64)
        for active, bt, mag in self._bursts_near(symbol, t):
            dt = t - bt
            on = active & (dt >= 0)
            ramp = np.clip(dt / BURST_RAMP_SECONDS, 0, 1)
            decay = np.exp(-np.clip(dt, 0, None) / BURST_DECAY_SECONDS)
            # jitter that is large right after the burst and fades quickly
            jit_salt = _salt(self.seed, symbol, "jit")
            jitter = (
                (_uniform(np.floor(t / 5), jit_salt) * 2 - 1)
                * abs(mag)
                * 0.25
                * np.exp(-np.clip(dt, 0, None) / 600)
            )
            out += np.where(on, mag * ramp * decay + jitter, 0.0)
        return out

    def _burst_intensity(self, symbol: str, t: np.ndarray) -> np.ndarray:
        """0..1 how close we are to a recent burst (used to widen spreads)."""
        out = np.zeros_like(t, dtype=np.float64)
        for active, bt, _ in self._bursts_near(symbol, t, lookback_hours=2):
            dt = t - bt
            out = np.maximum(
                out, np.where(active & (dt >= -30) & (dt < 300), np.exp(-np.clip(dt, 0, None) / 90), 0.0)
            )
        return out

    def _spec(self, symbol: str) -> InstrumentSpec:
        return self.registry.get(symbol)

    def bid_at(self, symbol: str, t: np.ndarray) -> np.ndarray:
        base = BASE_PRICES.get(symbol)
        if base is None:
            raise KeyError(f"synthetic generator has no base price for {symbol}")
        return base * np.exp(self._log_offset(symbol, t))

    def spread_at(self, symbol: str, t: np.ndarray) -> np.ndarray:
        spec = self._spec(symbol)
        base = BASE_SPREAD_PIPS.get(symbol, 1.5) * spec.pip_size
        return base * (1 + 6 * self._burst_intensity(symbol, t))

    # --- MarketDataProvider ------------------------------------------------
    def get_quote(self, symbol: str, now: datetime) -> Quote:
        spec = self._spec(symbol)
        t = np.array([now.timestamp()])
        bid = spec.round_price(float(self.bid_at(symbol, t)[0]))
        spread = max(spec.tick_size, spec.round_price(float(self.spread_at(symbol, t)[0])))
        return Quote(symbol, bid, spec.round_price(bid + spread), now.astimezone(UTC), DataSource.SYNTHETIC)

    def market_status(self, symbol: str, now: datetime) -> MarketStatus:
        return fx_market_open(now)

    def rates(self, now: datetime) -> RateBook:
        t = np.array([now.timestamp()])
        return RateBook({s: float(self.bid_at(s, t)[0]) for s in BASE_PRICES})

    def candles_frame(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, skip_weekends: bool = True
    ) -> pd.DataFrame:
        """Candles with open time in [start, end), as a DataFrame (bid OHLC + spread)."""
        tf_min = TIMEFRAME_MINUTES[timeframe]
        spec = self._spec(symbol)
        start = start.astimezone(UTC).replace(second=0, microsecond=0)
        s0 = int(start.timestamp()) // 60 * 60
        e0 = int(end.astimezone(UTC).timestamp())
        if e0 <= s0:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "spread"])
        minutes = np.arange(s0, e0, 60, dtype=np.float64)
        sub = np.arange(0, 60, 10, dtype=np.float64)
        grid = (minutes[:, None] + sub[None, :]).ravel()
        grid = np.concatenate([grid, minutes + 59.999])
        n = len(minutes)
        px = self.bid_at(symbol, grid)
        inner = px[: n * len(sub)].reshape(n, len(sub))
        closes = px[n * len(sub) :]
        opens = inner[:, 0]
        highs = np.maximum(inner.max(axis=1), closes)
        lows = np.minimum(inner.min(axis=1), closes)
        spreads = self.spread_at(symbol, minutes + 30)
        df = pd.DataFrame(
            {
                "time": pd.to_datetime(minutes, unit="s", utc=True),
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "spread": spreads,
            }
        )
        if skip_weekends:
            ts = df["time"]
            wd, hr = ts.dt.day_of_week, ts.dt.hour
            closed = (wd == 5) | ((wd == 4) & (hr >= 22)) | ((wd == 6) & (hr < 22))
            df = df[~closed]
        if tf_min > 1:
            df = (
                df.set_index("time")
                .resample(f"{tf_min}min", label="left", closed="left", origin="epoch")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "spread": "mean"})
                .dropna()
                .reset_index()
            )
        for col in ("open", "high", "low", "close"):
            df[col] = np.round(np.round(df[col] / spec.tick_size) * spec.tick_size, spec.digits)
        df["spread"] = np.maximum(
            np.round(np.round(df["spread"] / spec.tick_size) * spec.tick_size, spec.digits), spec.tick_size
        )
        return df.reset_index(drop=True)

    def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        tf = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
        # Only fully-closed candles: the last candle must end at or before ``end``.
        last_open_allowed = end - tf
        df = self.candles_frame(symbol, timeframe, start, end)
        out = []
        for row in df.itertuples(index=False):
            ts = row.time.to_pydatetime()
            if ts > last_open_allowed:
                continue
            out.append(
                Candle(
                    ts, float(row.open), float(row.high), float(row.low), float(row.close), float(row.spread)
                )
            )
        return out

    def burst_times(self, symbol: str, start: datetime, end: datetime) -> list[datetime]:
        """Times of synthetic volatility bursts (for diagnostics / tests)."""
        hours = np.arange(
            math.floor(start.timestamp() / 3600), math.ceil(end.timestamp() / 3600), dtype=np.float64
        )
        s_p = _salt(self.seed, symbol, "burst_p")
        s_t = _salt(self.seed, symbol, "burst_t")
        active = _uniform(hours, s_p) < BURST_PROBABILITY_PER_HOUR
        bt = hours * 3600 + _uniform(hours, s_t) * 3600
        return [
            datetime.fromtimestamp(float(x), UTC)
            for x in bt[active]
            if start.timestamp() <= x < end.timestamp()
        ]
