"""Strategy-validation helpers built on the backtester.

None of these results authorise live trading. They exist to expose
over-fitting, cost sensitivity and fragile parameter choices.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from trading.backtesting.engine import BacktestError, run_backtest
from trading.backtesting.models import BacktestConfig
from trading.instruments.spec import InstrumentSpec
from trading.news.models import EconomicEvent
from trading.risk.daily import max_drawdown

KEY_METRICS = (
    "trade_count",
    "cycle_count",
    "net_pnl",
    "return_pct",
    "max_drawdown_pct",
    "win_rate",
    "profit_factor",
    "expectancy_per_trade",
    "expectancy_per_cycle",
    "sharpe_ratio",
)


def _summary(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k) for k in KEY_METRICS}


def _with(cfg: BacktestConfig, **changes: Any) -> BacktestConfig:
    d = cfg.model_dump()
    for path, value in changes.items():
        cur = d
        parts = path.split("__")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
    return BacktestConfig(**d)


def split_point(cfg: BacktestConfig, in_sample_ratio: float) -> datetime:
    if not 0.1 <= in_sample_ratio <= 0.9:
        raise ValueError("in_sample_ratio must be between 0.1 and 0.9")
    return cfg.start + (cfg.end - cfg.start) * in_sample_ratio


def in_out_of_sample(
    cfg: BacktestConfig,
    data: pd.DataFrame,
    spec: InstrumentSpec,
    events: list[EconomicEvent] | None = None,
    in_sample_ratio: float = 0.7,
) -> dict[str, Any]:
    """Chronological split: the out-of-sample period starts strictly after the in-sample period ends."""
    cut = split_point(cfg, in_sample_ratio)
    ins = run_backtest(_with(cfg, end=cut), data, spec, events)
    oos = run_backtest(_with(cfg, start=cut), data, spec, events)
    return {
        "split_at": cut.isoformat(),
        "in_sample": _summary(ins.metrics),
        "out_of_sample": _summary(oos.metrics),
        "warnings": oos.warnings,
    }


def _set_param(strategy: dict, path: str, value: Any) -> dict:
    s = copy.deepcopy(strategy)
    cur = s
    parts = path.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
    return s


def parameter_sensitivity(
    cfg: BacktestConfig,
    data: pd.DataFrame,
    spec: InstrumentSpec,
    param: str,
    values: list[Any],
    events: list[EconomicEvent] | None = None,
) -> list[dict[str, Any]]:
    """Vary one strategy parameter (dotted path into ``cfg.strategy``)."""
    out = []
    for v in values:
        try:
            r = run_backtest(_with(cfg, strategy=_set_param(cfg.strategy, param, v)), data, spec, events)
            out.append({"value": v, **_summary(r.metrics)})
        except (BacktestError, ValueError) as exc:
            out.append({"value": v, "error": str(exc)})
    return out


def cost_sensitivity(
    cfg: BacktestConfig,
    data: pd.DataFrame,
    spec: InstrumentSpec,
    spread_multipliers=(1.0, 1.5, 2.0, 3.0),
    events: list[EconomicEvent] | None = None,
) -> list[dict[str, Any]]:
    out = []
    for mult in spread_multipliers:
        r = run_backtest(_with(cfg, costs__spread_multiplier=mult), data, spec, events)
        out.append({"spread_multiplier": mult, **_summary(r.metrics)})
    return out


def slippage_stress(
    cfg: BacktestConfig,
    data: pd.DataFrame,
    spec: InstrumentSpec,
    slippage_points=(0, 2, 5, 10, 20),
    events: list[EconomicEvent] | None = None,
) -> list[dict[str, Any]]:
    out = []
    for sp in slippage_points:
        r = run_backtest(_with(cfg, costs__slippage_points=sp), data, spec, events)
        out.append({"slippage_points": sp, **_summary(r.metrics)})
    return out


def drawdown_stress(
    trade_pnls: list[float], initial_balance: float, runs: int = 1000, seed: int = 1
) -> dict[str, Any]:
    """Monte-Carlo reshuffle of trade order: distribution of max drawdown."""
    if len(trade_pnls) < 5:
        return {"runs": 0, "warning": "fewer than 5 trades; drawdown stress test not meaningful"}
    rng = np.random.default_rng(int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16))
    pnl = np.array(trade_pnls, dtype=float)
    dds = []
    for _ in range(runs):
        eq = initial_balance + np.cumsum(rng.permutation(pnl))
        dds.append(max_drawdown([initial_balance, *eq.tolist()])[1])
    arr = np.array(dds)
    return {
        "runs": runs,
        "max_dd_pct_median": round(float(np.median(arr)), 3),
        "max_dd_pct_p95": round(float(np.percentile(arr, 95)), 3),
        "max_dd_pct_worst": round(float(arr.max()), 3),
    }


def baseline_comparison(
    cfg: BacktestConfig, data: pd.DataFrame, spec: InstrumentSpec, events: list[EconomicEvent] | None = None
) -> dict[str, Any]:
    mode = "no_trailing" if cfg.bot_kind.value == "news_volatility" else "random_direction"
    strat = run_backtest(cfg, data, spec, events)
    base = run_backtest(_with(cfg, baseline_mode=mode), data, spec, events)
    return {"baseline_mode": mode, "strategy": _summary(strat.metrics), "baseline": _summary(base.metrics)}


def walk_forward(
    cfg: BacktestConfig,
    data: pd.DataFrame,
    spec: InstrumentSpec,
    param_grid: dict[str, list[Any]],
    folds: int = 4,
    train_ratio: float = 0.7,
    objective: str = "net_pnl",
    events: list[EconomicEvent] | None = None,
) -> dict[str, Any]:
    """Rolling walk-forward: optimise on each train window, evaluate on the following test window."""
    if folds < 2:
        raise ValueError("walk-forward needs at least 2 folds")
    span = (cfg.end - cfg.start) / folds
    combos = [
        dict(zip(param_grid, vals, strict=True)) for vals in itertools.product(*param_grid.values())
    ] or [{}]
    if len(combos) > 50:
        raise ValueError("parameter grid too large (max 50 combinations)")
    results = []
    for k in range(folds):
        f_start = cfg.start + span * k
        f_end = f_start + span
        cut = f_start + (f_end - f_start) * train_ratio
        best, best_score = None, None
        for combo in combos:
            strat = cfg.strategy
            for p, v in combo.items():
                strat = _set_param(strat, p, v)
            try:
                r = run_backtest(_with(cfg, start=f_start, end=cut, strategy=strat), data, spec, events)
            except BacktestError:
                continue
            score = r.metrics.get(objective)
            if score is not None and (best_score is None or score > best_score):
                best, best_score = (combo, strat), score
        if best is None:
            results.append({"fold": k, "error": "no valid parameter combination"})
            continue
        try:
            test = run_backtest(_with(cfg, start=cut, end=f_end, strategy=best[1]), data, spec, events)
            results.append(
                {
                    "fold": k,
                    "train": [f_start.isoformat(), cut.isoformat()],
                    "test_window": [cut.isoformat(), f_end.isoformat()],
                    "chosen_params": best[0],
                    "train_score": best_score,
                    "test": _summary(test.metrics),
                }
            )
        except BacktestError as exc:
            results.append({"fold": k, "error": str(exc)})
    oos = [r["test"]["net_pnl"] for r in results if "test" in r and isinstance(r["test"], dict)]
    return {
        "folds": results,
        "oos_net_pnl_total": round(sum(oos), 2) if oos else None,
        "objective": objective,
    }
