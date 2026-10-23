from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest

from trading.adapters.simulated_broker import SimConfig, SimulatedBroker
from trading.execution.records import LegStatus
from trading.execution.single_entry import SingleTradeExecutor
from trading.instruments.spec import InstrumentRegistry
from trading.interfaces.types import Side
from trading.market_data.csv_importer import ImportFormatError, load_candles_csv
from trading.market_data.quality import check_candles
from trading.market_data.synthetic import SyntheticMarketData
from trading.risk.config import GlobalRiskConfig
from trading.risk.sizing import LegPlan
from trading.strategies.daily_trading.config import DailyTradingConfig
from trading.tests.conftest import new_cycle

SPEC = InstrumentRegistry.with_defaults().get("EURUSD")


def test_daily_trade_tp_and_break_even(broker, market, store, registry, clock):
    cfg = DailyTradingConfig(break_even={"enabled": True, "trigger_r": 1.0, "offset_pips": 0.5})
    ex = SingleTradeExecutor(broker, store, registry.get("EURUSD"), clock, GlobalRiskConfig(), cfg)
    c = ex.open(
        new_cycle(clock, kind="single", state="validating", bot_id="daily", magic=2001),
        LegPlan(Side.BUY, 1.10010, 1.09810),
        0.2,
        1.10410,
    )
    assert c.state == "open" and c.legs[0].confirmed_sl == pytest.approx(1.09810)
    clock.advance(60)
    market.set("EURUSD", 1.10220, 1.10230)  # +1R
    ex.manage(c)
    assert c.legs[0].confirmed_sl == pytest.approx(1.10015)  # break-even + 0.5 pip
    clock.advance(60)
    market.set("EURUSD", 1.10420, 1.10430)
    broker.process_market()
    ex.manage(c)
    assert c.state == "closed" and c.legs[0].exit_reason == "take_profit" and c.realized_pnl > 0


def test_daily_trade_max_holding_closes(broker, market, store, registry, clock):
    cfg = DailyTradingConfig(max_holding_minutes=30)
    ex = SingleTradeExecutor(broker, store, registry.get("EURUSD"), clock, GlobalRiskConfig(), cfg)
    c = ex.open(
        new_cycle(clock, kind="single", bot_id="daily", magic=2001),
        LegPlan(Side.SELL, 1.10000, 1.10200),
        0.1,
        None,
    )
    clock.advance(31 * 60)
    market.set("EURUSD", 1.10000, 1.10010)
    ex.manage(c)
    assert (
        c.state == "closed"
        and c.legs[0].exit_reason == "max_duration"
        and c.legs[0].status is LegStatus.CLOSED
    )


def test_daily_trade_rejected_order(broker, store, registry, clock):
    broker.inject = ["reject"]
    ex = SingleTradeExecutor(
        broker, store, registry.get("EURUSD"), clock, GlobalRiskConfig(), DailyTradingConfig()
    )
    c = ex.open(
        new_cycle(clock, kind="single", bot_id="daily", magic=2001),
        LegPlan(Side.BUY, 1.10010, 1.09810),
        0.1,
        None,
    )
    assert c.state == "failed" and broker.list_positions() == []


def test_simulator_deterministic_with_seed(market, clock, registry):
    from trading.adapters.base import OrderRequest

    def run():
        b = SimulatedBroker(market, clock, SimConfig(seed=5, slippage_points_max=5, latency_ms=0), registry)
        return [
            b.submit_market_order(OrderRequest(f"c{i}", "EURUSD", Side.BUY, 0.1, 1.09)).fill_price
            for i in range(5)
        ]

    assert run() == run()


def test_simulator_state_roundtrip(broker, market, clock, registry):
    from trading.adapters.base import OrderRequest

    broker.submit_market_order(OrderRequest("x1", "EURUSD", Side.BUY, 0.3, 1.09))
    b2 = SimulatedBroker(market, clock, broker.config, registry, state=broker.to_state())
    assert len(b2.list_positions()) == 1 and b2.account_info().balance == broker.account_info().balance


# --- data quality / import ---------------------------------------------------------------
def test_quality_checks_detect_problems():
    df = SyntheticMarketData(seed=1).candles_frame(
        "EURUSD", "M1", datetime(2025, 1, 6, tzinfo=UTC), datetime(2025, 1, 7, tzinfo=UTC)
    )
    assert check_candles(df, "M1", SPEC).ok
    bad = df.copy()
    bad.loc[5, "high"] = bad.loc[5, "low"] - 0.001
    bad = __import__("pandas").concat([bad, bad.iloc[[10]]]).reset_index(drop=True)
    codes = {i.code for i in check_candles(bad, "M1", SPEC).issues}
    assert {"invalid_ohlc", "duplicates", "out_of_order"} <= codes
    jpy = df.copy()
    jpy[["open", "high", "low", "close"]] *= 100
    assert "price_scale" in {i.code for i in check_candles(jpy, "M1", SPEC).issues}


def test_csv_import_timezone_conversion():
    csv = "time,open,high,low,close\n2025-01-06 10:00:00,1.1,1.1002,1.0999,1.1001\n2025-01-06 10:01:00,1.1001,1.1003,1.1,1.1002\n"
    df = load_candles_csv(io.StringIO(csv), SPEC, source_tz="Europe/Berlin")
    assert df["time"].iloc[0].hour == 9 and str(df["time"].dt.tz) == "UTC"
    with pytest.raises(ImportFormatError):
        load_candles_csv(io.StringIO("a,b\n1,2\n"), SPEC)


def test_mt5_export_format_import():
    csv = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n2025.01.06\t10:00:00\t1.1\t1.1002\t1.0999\t1.1001\t10\t0\t8\n"
    df = load_candles_csv(io.StringIO(csv), SPEC, "UTC")
    assert df["spread"].iloc[0] == pytest.approx(0.00008)


def test_synthetic_quotes_labelled_and_deterministic():
    m1, m2 = SyntheticMarketData(seed=3), SyntheticMarketData(seed=3)
    t = datetime(2025, 2, 3, 12, tzinfo=UTC)
    a, b = m1.get_quote("EURUSD", t), m2.get_quote("EURUSD", t)
    assert a == b and a.source.value == "synthetic" and a.ask > a.bid
    assert m1.get_quote("EURUSD", t + timedelta(seconds=10)) != a


def test_sample_calendar_file_loads_and_is_labelled(tmp_path):
    import json

    from trading.news.provider import SampleCalendarProvider

    f = tmp_path / "ev.json"
    f.write_text(
        json.dumps(
            [{"event_id": "x", "currency": "usd", "name": "CPI", "scheduled_at": "2025-01-15T13:30:00Z"}]
        )
    )
    prov = SampleCalendarProvider(f)
    evs = prov.events(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC))
    assert (
        len(evs) == 1
        and evs[0].origin.value == "sample"
        and evs[0].currency == "USD"
        and evs[0].fetched_at is not None
    )
    assert "not real" in prov.health(datetime.now(UTC)).detail
