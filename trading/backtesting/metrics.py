"""Performance metrics. Formulas are documented in docs/BACKTESTING.md.

Every ratio returns ``None`` when its denominator is zero or the sample is
too small, instead of a misleading number.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

from trading.risk.daily import max_drawdown

MIN_TRADES_FOR_STATS = 30
MIN_DAYS_FOR_SHARPE = 20


def _safe_div(a: float, b: float) -> float | None:
    return None if b == 0 else a / b


def _round(x: float | None, n: int = 4) -> float | None:
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(x, n)


def max_consecutive(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def compute_metrics(
    trades: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
    equity_curve: list[tuple[datetime, float]],
    initial_balance: float,
    period_start: datetime,
    period_end: datetime,
    daily_equity: list[tuple[datetime, float]] | None = None,
) -> dict[str, Any]:
    """``trades``: closed legs with net_pnl, gross_pnl, commission, swap, entry/exit times, symbol, trigger.
    ``cycles``: parent records (for dual-entry; for single trades a cycle == a trade)."""
    net = [t["net_pnl"] for t in trades]
    gross = [t["gross_pnl"] for t in trades]
    costs = [-(t["commission"] + t["swap"]) for t in trades]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    total_net = float(sum(net))
    total_gross = float(sum(gross))
    final_equity = initial_balance + total_net
    eq_values = [e for _, e in equity_curve] or [initial_balance]
    mdd, mdd_pct = max_drawdown([initial_balance, *eq_values])

    cyc_net = [c["net_pnl"] for c in cycles]
    holding = [
        (t["exit_time"] - t["entry_time"]).total_seconds() / 60
        for t in trades
        if t.get("exit_time") and t.get("entry_time")
    ]
    exposure_minutes = _union_minutes(
        [(t["entry_time"], t["exit_time"]) for t in trades if t.get("exit_time")]
    )
    period_minutes = max(1.0, (period_end - period_start).total_seconds() / 60)

    m: dict[str, Any] = {
        "trade_count": len(trades),
        "cycle_count": len(cycles),
        "net_pnl": round(total_net, 2),
        "gross_pnl": round(total_gross, 2),
        "total_costs": round(float(sum(costs)), 2),
        "final_equity": round(final_equity, 2),
        "return_pct": _round(total_net / initial_balance * 100, 3),
        "max_drawdown": round(mdd, 2),
        "max_drawdown_pct": _round(mdd_pct, 3),
        "win_rate": _round(_safe_div(len(wins), len(net)), 4),
        "profit_factor": _round(_safe_div(sum(wins), abs(sum(losses))), 3) if wins or losses else None,
        "expectancy_per_trade": _round(_safe_div(total_net, len(net)), 2),
        "expectancy_per_cycle": _round(_safe_div(float(sum(cyc_net)), len(cyc_net)), 2),
        "cycle_win_rate": _round(_safe_div(len([x for x in cyc_net if x > 0]), len(cyc_net)), 4),
        "average_winner": _round(float(np.mean(wins)), 2) if wins else None,
        "average_loser": _round(float(np.mean(losses)), 2) if losses else None,
        "largest_loss": _round(float(min(net)), 2) if net else None,
        "largest_win": _round(float(max(net)), 2) if net else None,
        "max_consecutive_losses": max_consecutive([x <= 0 for x in (cyc_net or net)]),
        "average_holding_minutes": _round(float(np.mean(holding)), 1) if holding else None,
        "exposure_pct": _round(exposure_minutes / period_minutes * 100, 2),
        "costs_pct_of_gross": _round(float(sum(costs)) / abs(total_gross) * 100, 2) if total_gross else None,
    }

    # Risk-adjusted metrics from daily equity returns.
    m["sharpe_ratio"] = None
    m["sortino_ratio"] = None
    if daily_equity and len(daily_equity) > MIN_DAYS_FOR_SHARPE:
        vals = np.array([e for _, e in daily_equity], dtype=float)
        rets = np.diff(vals) / vals[:-1]
        if len(rets) > 1 and rets.std(ddof=1) > 0:
            m["sharpe_ratio"] = _round(float(rets.mean() / rets.std(ddof=1) * math.sqrt(252)), 3)
            downside = rets[rets < 0]
            if len(downside) > 1 and downside.std(ddof=1) > 0:
                m["sortino_ratio"] = _round(float(rets.mean() / downside.std(ddof=1) * math.sqrt(252)), 3)

    m["by_instrument"] = _group(trades, lambda t: t["symbol"])
    m["by_trigger"] = _group(trades, lambda t: t.get("trigger", "unknown"))
    m["by_month"] = _group(
        trades, lambda t: t["exit_time"].strftime("%Y-%m") if t.get("exit_time") else "open"
    )
    m["by_strategy_version"] = _group(trades, lambda t: t.get("strategy_version", "unknown"))
    m["by_exit_reason"] = _group(trades, lambda t: t.get("exit_reason") or "unknown")
    return m


def _group(trades: list[dict], key) -> dict[str, dict[str, Any]]:
    g: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        g[str(key(t))].append(t["net_pnl"])
    return {
        k: {
            "trades": len(v),
            "net_pnl": round(sum(v), 2),
            "win_rate": _round(len([x for x in v if x > 0]) / len(v), 4),
        }
        for k, v in sorted(g.items())
    }


def _union_minutes(intervals: list[tuple[datetime, datetime]]) -> float:
    if not intervals:
        return 0.0
    iv = sorted(intervals)
    total = 0.0
    cs, ce = iv[0]
    for s, e in iv[1:]:
        if s <= ce:
            ce = max(ce, e)
        else:
            total += (ce - cs).total_seconds()
            cs, ce = s, e
    total += (ce - cs).total_seconds()
    return total / 60


def sample_warnings(metrics: dict[str, Any]) -> list[str]:
    w = []
    n = metrics.get("trade_count", 0)
    if n == 0:
        w.append("No trades were generated; no performance conclusions are possible.")
    elif n < MIN_TRADES_FOR_STATS:
        w.append(
            f"Only {n} trades: the sample is too small for statistically meaningful conclusions (< {MIN_TRADES_FOR_STATS})."
        )
    wr = metrics.get("win_rate")
    pf = metrics.get("profit_factor")
    if wr is not None and wr > 0.6 and (pf is None or pf < 1):
        w.append(
            "High win rate with profit factor below 1: winners are smaller than losers. Win rate alone is not evidence of profitability."
        )
    return w
