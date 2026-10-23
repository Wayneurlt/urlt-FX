"""Data-quality checks for candle frames before they are used for backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from trading.instruments.spec import InstrumentSpec
from trading.market_data.models import TIMEFRAME_MINUTES


@dataclass
class QualityIssue:
    code: str
    severity: str  # "error" | "warning"
    message: str
    count: int = 0
    examples: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    rows: int
    issues: list[QualityIssue]

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def as_dict(self) -> dict:
        return {
            "rows": self.rows,
            "ok": self.ok,
            "issues": [i.__dict__ for i in self.issues],
        }


def _is_weekend_gap(a: pd.Timestamp, b: pd.Timestamp) -> bool:
    # A gap that starts Friday evening and ends Sunday evening/Monday is the normal FX weekend.
    return a.weekday() == 4 and b.weekday() in (6, 0) and (b - a) <= pd.Timedelta(days=3)


def check_candles(
    df: pd.DataFrame,
    timeframe: str,
    spec: InstrumentSpec | None = None,
    now: datetime | None = None,
    max_gap_multiple: int = 5,
) -> QualityReport:
    issues: list[QualityIssue] = []
    n = len(df)
    if n == 0:
        return QualityReport(0, [QualityIssue("empty", "error", "dataset contains no rows")])

    t = df["time"]
    if getattr(t.dt, "tz", None) is None:
        issues.append(QualityIssue("naive_timezone", "error", "timestamps have no timezone; expected UTC", n))
        return QualityReport(n, issues)

    dup = t.duplicated()
    if dup.any():
        issues.append(
            QualityIssue(
                "duplicates",
                "error",
                "duplicate timestamps",
                int(dup.sum()),
                [str(x) for x in t[dup].head(5)],
            )
        )

    diffs = t.diff().iloc[1:]
    back = diffs <= pd.Timedelta(0)
    if back.any():
        issues.append(
            QualityIssue("out_of_order", "error", "timestamps not strictly increasing", int(back.sum()))
        )

    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    misaligned = (t.astype("int64") // 10**9) % int(tf.total_seconds()) != 0
    if misaligned.any() and timeframe != "D1":
        issues.append(
            QualityIssue(
                "misaligned",
                "warning",
                f"timestamps not aligned to {timeframe} boundaries (timezone conversion?)",
                int(misaligned.sum()),
            )
        )

    big = diffs > tf * max_gap_multiple
    if big.any():
        idx = np.where(big.to_numpy())[0]
        real_gaps = [(t.iloc[i], t.iloc[i + 1]) for i in idx if not _is_weekend_gap(t.iloc[i], t.iloc[i + 1])]
        if real_gaps:
            issues.append(
                QualityIssue(
                    "gaps",
                    "warning",
                    f"gaps longer than {max_gap_multiple} bars (excluding weekends)",
                    len(real_gaps),
                    [f"{a} -> {b}" for a, b in real_gaps[:5]],
                )
            )

    o, h, lo, c = (df[k].astype(float) for k in ("open", "high", "low", "close"))
    nonpos = (o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0)
    if nonpos.any():
        issues.append(QualityIssue("non_positive", "error", "non-positive prices", int(nonpos.sum())))
    bad = (h < np.maximum(o, c)) | (lo > np.minimum(o, c)) | (h < lo)
    if bad.any():
        issues.append(
            QualityIssue(
                "invalid_ohlc",
                "error",
                "high/low inconsistent with open/close",
                int(bad.sum()),
                [str(x) for x in t[bad].head(5)],
            )
        )
    nans = df[["open", "high", "low", "close"]].isna().any(axis=1)
    if nans.any():
        issues.append(QualityIssue("missing_values", "error", "missing OHLC values", int(nans.sum())))

    if "spread" in df.columns:
        neg = df["spread"] < 0
        if neg.any():
            issues.append(QualityIssue("negative_spread", "error", "negative spread", int(neg.sum())))
    else:
        issues.append(
            QualityIssue(
                "no_spread", "warning", "no spread column; the backtest spread model will be used", n
            )
        )

    if spec is not None:
        scaled = c / spec.tick_size
        off_tick = np.abs(scaled - np.round(scaled)) > 1e-6
        if off_tick.any():
            issues.append(
                QualityIssue(
                    "precision", "warning", f"prices not on tick grid {spec.tick_size}", int(off_tick.sum())
                )
            )
        # A wildly wrong price scale (e.g. JPY file loaded as EURUSD).
        med = float(c.median())
        if spec.quote_currency == "JPY" and med < 20 or spec.quote_currency != "JPY" and med > 20:
            issues.append(
                QualityIssue("price_scale", "error", f"median price {med} implausible for {spec.symbol}", n)
            )

    if now is not None:
        last_end = t.iloc[-1] + tf
        if last_end > pd.Timestamp(now):
            issues.append(
                QualityIssue(
                    "incomplete_candle", "warning", "last candle has not closed yet and will be ignored", 1
                )
            )

    return QualityReport(n, issues)


def check_quotes_freshness(quote_time: datetime, now: datetime, max_age_seconds: float) -> str | None:
    age = (now - quote_time).total_seconds()
    if age > max_age_seconds:
        return f"quote is stale ({age:.1f}s old > {max_age_seconds}s)"
    if age < -2:
        return f"quote timestamp is {abs(age):.1f}s in the future (clock or timezone error)"
    return None


def check_tick_order(times: list[datetime]) -> int:
    """Number of out-of-order ticks."""
    return sum(1 for a, b in zip(times, times[1:], strict=False) if b < a)


__all__ = [
    "QualityIssue",
    "QualityReport",
    "check_candles",
    "check_quotes_freshness",
    "check_tick_order",
    "timedelta",
]
