"""Cross-component integration tests: API + runtime + risk + simulator + persistence."""

from __future__ import annotations

import threading

from sqlalchemy import select

from app.db.base import session_scope
from app.models import RiskReservation, SimBrokerState, TradingCycle
from app.services.trading_runtime import manual_entry, run_tick
from trading.adapters.simulated_broker import SimulatedBroker, sim_config_from_dict
from trading.instruments.spec import InstrumentRegistry
from trading.interfaces.types import Environment
from trading.market_data.synthetic import SyntheticMarketData


def _tighten(api, max_cycles: int) -> None:
    cfg = api.get("/risk/config").json()["config"]
    cfg["global"]["max_concurrent_cycles"] = max_cycles
    cfg["news_volatility"]["max_concurrent_cycles"] = max_cycles
    cfg["daily_trading"]["max_concurrent_trades"] = max_cycles
    assert api.put("/risk/config", json={"config": cfg}).status_code == 200


def test_two_bots_cannot_both_consume_the_risk_budget(loose_spreads):
    api = loose_spreads
    _tighten(api, 1)
    api.post("/bots/news-volatility/actions/enable")
    api.post("/bots/daily-trading/actions/enable")
    results: list[dict] = []

    def go(bot, symbol, side):
        try:
            results.append(manual_entry(bot, symbol, side, 20, None, "admin"))
        except Exception as exc:  # noqa: BLE001 - lease contention surfaces as an API error
            results.append({"approved": False, "reasons": [str(exc)]})

    threads = [
        threading.Thread(target=go, args=("news-volatility", "EURUSD", None)),
        threading.Thread(target=go, args=("daily-trading", "USDJPY", "BUY")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sum(1 for r in results if r["approved"]) == 1, results
    with session_scope() as s:
        assert len(s.scalars(select(RiskReservation).where(RiskReservation.active.is_(True))).all()) == 1


def test_db_and_simulator_agree_after_ticks(loose_spreads):
    """Reconciliation invariant: every open leg in the DB exists at the (persisted) simulator and vice versa."""
    api = loose_spreads
    api.post("/bots/news-volatility/actions/enable")
    api.post("/bots/daily-trading/actions/enable")
    api.post("/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 10})
    api.post(
        "/paper/manual-entry",
        json={
            "bot_id": "daily-trading",
            "symbol": "GBPUSD",
            "side": "SELL",
            "stop_pips": 12,
            "target_pips": 15,
        },
    )
    for _ in range(3):
        run_tick(Environment.SIMULATION)
    with session_scope() as s:
        row = s.get(SimBrokerState, "simulation")
        from app.db.base import utcnow

        reg = InstrumentRegistry.with_defaults()
        sim = SimulatedBroker(
            SyntheticMarketData(7, reg), utcnow, sim_config_from_dict(row.settings), reg, row.state
        )
        broker_ids = {p.position_id for p in sim.list_positions()}
        db_ids = {
            lg.position_id
            for c in s.scalars(select(TradingCycle).where(TradingCycle.active.is_(True)))
            for lg in c.legs
            if lg.status == "open"
        }
        assert broker_ids == db_ids
        # every simulated position carries a stop
        assert all(p.sl is not None for p in sim.list_positions())


def test_restart_recovers_open_cycle_without_new_orders(loose_spreads):
    api = loose_spreads
    api.post("/bots/news-volatility/actions/enable")
    cid = api.post(
        "/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15}
    ).json()["cycle_id"]
    with session_scope() as s:
        n_orders = len(s.get(SimBrokerState, "simulation").state["orders"])
    # A fresh process would rebuild everything from the DB; run_tick does exactly that.
    run_tick(Environment.SIMULATION)
    with session_scope() as s:
        assert len(s.get(SimBrokerState, "simulation").state["orders"]) == n_orders
        assert s.get(TradingCycle, cid).state in (
            "both_legs_open",
            "one_leg_stopped",
            "trailing_active",
            "completed",
        )
