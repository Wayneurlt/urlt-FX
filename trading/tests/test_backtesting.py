from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading.backtesting.engine import Backtester, BacktestError, run_backtest
from trading.backtesting.metrics import compute_metrics
from trading.backtesting.models import BacktestConfig
from trading.backtesting.validation import drawdown_stress, in_out_of_sample
from trading.instruments.spec import InstrumentRegistry
from trading.interfaces.types import Side
from trading.market_data.synthetic import SyntheticMarketData
from trading.news.models import EconomicEvent, EventOrigin, Impact

SPEC = InstrumentRegistry.with_defaults().get("EURUSD")
S, E = datetime(2025, 1, 6, tzinfo=UTC), datetime(2025, 1, 27, tzinfo=UTC)


@pytest.fixture(scope="module")
def data():
    return SyntheticMarketData(seed=7).candles_frame("EURUSD", "M1", S, E)


def cfg(kind="news_volatility", **kw):
    strategy = kw.pop(
        "strategy", {"entry_mode": {"news": False, "volatility": True}} if kind == "news_volatility" else {}
    )
    kw.setdefault("start", S)
    kw.setdefault("end", E)
    return BacktestConfig(bot_kind=kind, symbol="EURUSD", strategy=strategy, **kw)


def flat_frame(n=200, price=1.1, spread=0.0001, start=S):
    t = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "time": t,
            "open": price,
            "high": price + 0.0001,
            "low": price - 0.0001,
            "close": price,
            "spread": spread,
        }
    )


def test_reproducible(data):
    a = run_backtest(cfg(), data, SPEC)
    b = run_backtest(cfg(), data, SPEC)
    assert a.metrics["net_pnl"] == b.metrics["net_pnl"] and a.data_fingerprint == b.data_fingerprint
    c = run_backtest(cfg(seed=99), data, SPEC)
    assert c.config["seed"] == 99


def test_dual_entry_legs_have_different_fills_and_costs(data):
    r = run_backtest(cfg(), data, SPEC)
    assert r.cycles, "expected volatility cycles on synthetic data"
    by_cycle: dict[str, list] = {}
    for t in r.trades:
        by_cycle.setdefault(t["cycle_id"], []).append(t)
    two = [v for v in by_cycle.values() if len(v) == 2]
    assert two
    for legs in two:
        buy = next(x for x in legs if x["side"] == "BUY")
        sell = next(x for x in legs if x["side"] == "SELL")
        assert buy["entry_price"] > sell["entry_price"]  # BUY at ask, SELL at bid
        assert buy["commission"] < 0 and sell["commission"] < 0
    # cycles are not double counted
    assert r.metrics["cycle_count"] == len(r.cycles) <= r.metrics["trade_count"]


def test_no_look_ahead_future_data_does_not_change_past_trades(data):
    full = run_backtest(cfg(), data, SPEC)
    cut = S + timedelta(days=12)
    part = run_backtest(cfg(end=cut), data, SPEC)
    done_before = [
        t
        for t in full.trades
        if t["exit_time"] < (cut - timedelta(days=1)).isoformat() and t["exit_reason"] != "end_of_data"
    ]
    part_trades = {(t["entry_time"], t["side"]): t["net_pnl"] for t in part.trades}
    assert done_before
    for t in done_before:
        assert part_trades.get((t["entry_time"], t["side"])) == t["net_pnl"]


def test_daily_strategy_runs_and_respects_session(data):
    r = run_backtest(cfg("daily_trading"), data, SPEC)
    for t in r.trades:
        et = datetime.fromisoformat(t["entry_time"])
        assert 7 <= et.hour < 16
        assert t["tp"] is not None
        if t["side"] == "BUY":
            assert t["initial_sl"] < t["entry_price"] < t["tp"]


def test_gap_through_stop_fills_at_open_not_stop():
    df = flat_frame(300)
    bt = Backtester(cfg(), df, SPEC)
    from trading.backtesting.engine import _Cycle, _Leg

    leg = _Leg(
        "c",
        Side.BUY,
        1.0,
        1.1001,
        1.1001,
        bt.t[0],
        sl=1.0990,
        tp=None,
        initial_sl=1.0990,
        trigger="volatility",
    )
    bt.o[10], bt.h[10], bt.lo[10], bt.c[10] = 1.0980, 1.0985, 1.0975, 1.0982  # opens below the stop
    px, why = bt._check_exit(leg, 10)
    assert why == "gap_stop" and px <= 1.0980
    cyc = _Cycle("c", "volatility", None, bt.t[0], legs=[leg])
    rec = bt._close_leg(cyc, leg, 10, px, "stop_loss")
    assert rec["exit_price"] < 1.0990  # worse than the stop


def test_stop_checked_before_target_in_same_bar():
    bt = Backtester(cfg(), flat_frame(300), SPEC)
    from trading.backtesting.engine import _Leg

    leg = _Leg("c", Side.BUY, 1.0, 1.1, 1.1, bt.t[0], sl=1.0995, tp=1.1005, initial_sl=1.0995, trigger="x")
    bt.h[20], bt.lo[20] = 1.1010, 1.0990
    _, why = bt._check_exit(leg, 20)
    assert why == "stop" and bt.counters["ambiguous_bars"] == 1


def test_short_stop_triggers_on_ask():
    bt = Backtester(cfg(costs={"spread_mode": "fixed", "fixed_spread_pips": 2.0}), flat_frame(300), SPEC)
    from trading.backtesting.engine import _Leg

    leg = _Leg("c", Side.SELL, 1.0, 1.1, 1.1, bt.t[0], sl=1.1003, tp=None, initial_sl=1.1003, trigger="x")
    # bid high 1.10015 never reaches 1.1003, but ask high = 1.10035 does
    bt.o[5], bt.h[5], bt.lo[5] = 1.1, 1.10015, 1.0999
    assert bt._check_exit(leg, 5) is not None


def test_costs_reduce_pnl(data):
    cheap = run_backtest(
        cfg(
            costs={
                "commission_per_lot_side": 0,
                "slippage_points": 0,
                "random_slippage_points": 0,
                "second_leg_extra_slippage_points": 0,
            }
        ),
        data,
        SPEC,
    )
    costly = run_backtest(cfg(costs={"commission_per_lot_side": 7, "slippage_points": 3}), data, SPEC)
    assert costly.metrics["net_pnl"] < cheap.metrics["net_pnl"]
    assert any("unrealistic" in w for w in cheap.warnings)


def test_missing_data_gap_blocks_entries():
    df = SyntheticMarketData(seed=7).candles_frame("EURUSD", "M1", S, S + timedelta(days=2))
    holey = df.drop(df.index[1000:1300]).reset_index(drop=True)
    bt = Backtester(cfg(end=S + timedelta(days=2)), holey, SPEC)
    assert bt._gap_before(1000)
    bt.run()


def test_news_events_trigger_entries(data):
    ev = [
        EconomicEvent(
            "e1",
            "USD",
            "CPI",
            datetime(2025, 1, 15, 13, 30, tzinfo=UTC),
            Impact.HIGH,
            "sample",
            EventOrigin.SAMPLE,
        )
    ]
    r = run_backtest(
        cfg(strategy={"entry_mode": {"news": True, "volatility": False}}, risk={"max_spread_pips": 50}),
        data,
        SPEC,
        ev,
    )
    assert len(r.cycles) == 1 and r.cycles[0]["event_id"] == "e1" and r.cycles[0]["trigger"] == "news"


def test_in_out_of_sample_is_chronological(data):
    res = in_out_of_sample(cfg(), data, SPEC, None, 0.6)
    assert datetime.fromisoformat(res["split_at"]) == S + (E - S) * 0.6


def test_metrics_with_zero_trades():
    m = compute_metrics([], [], [], 10_000, S, E)
    assert (
        m["trade_count"] == 0
        and m["win_rate"] is None
        and m["profit_factor"] is None
        and m["expectancy_per_trade"] is None
    )


def test_insufficient_data_raises():
    with pytest.raises(BacktestError):
        run_backtest(cfg(), flat_frame(10), SPEC)


def test_drawdown_stress():
    res = drawdown_stress([10, -20, 15, -5, 30, -25, 5], 1000, runs=200)
    assert res["max_dd_pct_p95"] >= res["max_dd_pct_median"] >= 0
    assert drawdown_stress([1, 2], 1000)["runs"] == 0


def test_conversion_for_cross_requires_rates():
    gbp = InstrumentRegistry.with_defaults().get("EURGBP")
    df = SyntheticMarketData(seed=7).candles_frame("EURGBP", "M1", S, S + timedelta(days=2))
    c = BacktestConfig(
        bot_kind="news_volatility",
        symbol="EURGBP",
        start=S,
        end=S + timedelta(days=2),
        strategy={
            "instruments": ["EURGBP"],
            "entry_mode": {"news": False, "volatility": True},
            "volatility": {"atr_ratio_min": 1.1, "range_multiple_min": 1.1, "expansion_min_pips": 0},
        },
        risk={"max_spread_pips": 50},
    )
    with pytest.raises(BacktestError):
        run_backtest(c, df, gbp)
    ok = run_backtest(c.model_copy(update={"conversion_rates": {"GBPUSD": 1.27, "EURUSD": 1.085}}), df, gbp)
    assert any("Fixed conversion rates" in w for w in ok.warnings)
    assert np.isfinite(ok.metrics["net_pnl"])
