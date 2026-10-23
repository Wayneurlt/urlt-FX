"""Daily-loss and drawdown definitions.

Daily loss is measured on EQUITY (balance + floating P/L):

    daily_loss = max(0, day_start_equity - current_equity)

Because realized P/L moves balance and floating P/L is the remainder of
equity, every loss is counted exactly once: when a floating loss is realized
it leaves "floating" and enters "balance" without changing equity.

    drawdown_pct = (peak_equity - current_equity) / peak_equity * 100
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def daily_reference_start(now: datetime, reset_hour_utc: int = 0) -> datetime:
    now = now.astimezone(UTC)
    start = now.replace(hour=reset_hour_utc, minute=0, second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)
    return start


def daily_loss(day_start_equity: float, current_equity: float) -> float:
    return max(0.0, day_start_equity - current_equity)


def daily_loss_pct(day_start_equity: float, current_equity: float) -> float:
    if day_start_equity <= 0:
        return 0.0
    return daily_loss(day_start_equity, current_equity) / day_start_equity * 100


def drawdown_pct(peak_equity: float, current_equity: float) -> float:
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - current_equity) / peak_equity * 100)


def max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    """(max drawdown in money, max drawdown in %) over an equity series."""
    peak = float("-inf")
    mdd = 0.0
    mdd_pct = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        dd = peak - e
        if dd > mdd:
            mdd = dd
        if peak > 0:
            mdd_pct = max(mdd_pct, dd / peak * 100)
    return mdd, mdd_pct
