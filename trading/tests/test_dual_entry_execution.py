from __future__ import annotations

import pytest

from trading.adapters.base import OwnershipViolation, UnsupportedAccountMode
from trading.adapters.simulated_broker import SimConfig, SimulatedBroker
from trading.execution.dual_entry import DualEntryExecutor
from trading.execution.records import IntentStatus, LegStatus
from trading.interfaces.types import MarginMode, Side
from trading.risk.circuit_breakers import BreakerType
from trading.risk.config import GlobalRiskConfig
from trading.risk.sizing import LegPlan
from trading.tests.conftest import new_cycle


def plans(bid=1.10000, ask=1.10010, stop_pips=15):
    d = stop_pips * 0.0001
    return [LegPlan(Side.BUY, ask, round(ask - d, 5)), LegPlan(Side.SELL, bid, round(bid + d, 5))]


def make(broker, store, registry, clock, nv_config, risk=None):
    return DualEntryExecutor(
        broker, store, registry.get("EURUSD"), clock, risk or GlobalRiskConfig(), nv_config
    )


def test_both_legs_filled_with_verified_stops(broker, store, registry, clock, nv_config):
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.state == "both_legs_open"
    buy, sell = c.leg(Side.BUY), c.leg(Side.SELL)
    assert buy.fill_price == 1.10010 and sell.fill_price == 1.10000  # ask vs bid
    assert buy.confirmed_sl == pytest.approx(1.09860) and sell.confirmed_sl == pytest.approx(1.10150)
    assert len(broker.list_positions()) == 2
    # Intents were persisted before submission
    assert (
        store.state_history[c.id][:2] == ["validating", "submitting_orders"]
        or store.state_history[c.id][0] == "submitting_orders"
    )


def test_buy_filled_sell_rejected_closes_unmatched_leg(broker, store, registry, clock, nv_config):
    broker.inject = [None, "reject"]  # first order normal, second rejected
    broker.inject = ["", "reject"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.leg(Side.SELL).intent_status is IntentStatus.REJECTED
    assert c.leg(Side.BUY).status is LegStatus.CLOSED
    assert c.leg(Side.BUY).exit_reason == "unmatched_exposure"
    assert c.state == "completed" and c.outcome == "unmatched_closed"
    assert broker.list_positions() == []
    assert "unmatched_exposure" in store.kinds()


def test_sell_filled_buy_rejected(broker, store, registry, clock, nv_config):
    broker.inject = ["reject"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.leg(Side.BUY).intent_status is IntentStatus.REJECTED
    assert c.leg(Side.SELL).status is LegStatus.CLOSED
    assert c.state == "completed"
    assert broker.list_positions() == []


def test_unmatched_keep_protected_policy(broker, store, registry, clock, nv_config):
    broker.inject = ["reject"]
    ex = make(
        broker, store, registry, clock, nv_config, GlobalRiskConfig(unmatched_leg_policy="keep_protected")
    )
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.state == "one_leg_filled"
    assert len(broker.list_positions()) == 1


def test_timeout_but_filled_is_reconciled_not_resubmitted(broker, store, registry, clock, nv_config):
    broker.inject = ["timeout_filled"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.leg(Side.BUY).intent_status is IntentStatus.FILLED
    assert c.state == "both_legs_open"
    assert len(broker.list_positions()) == 2  # no duplicate BUY
    assert "order_reconciled" in store.kinds()


def test_timeout_not_filled_is_reconciled_as_not_executed(broker, store, registry, clock, nv_config):
    broker.inject = ["timeout_not_filled"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.leg(Side.BUY).intent_status is IntentStatus.NOT_EXECUTED
    # SELL filled alone -> unmatched -> closed
    assert c.state == "completed"
    assert broker.list_positions() == []


def test_connection_loss_during_submission_halts(broker, store, registry, clock, nv_config):
    broker.inject = ["", "disconnect"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.state == "emergency_halt"
    assert any(b[0] is BreakerType.UNCERTAIN_ORDER_STATE for b in store.breakers)
    # Reconnect: management reconciles without re-submitting
    broker.connect()
    ex.manage(c)
    assert c.leg(Side.SELL).intent_status is IntentStatus.NOT_EXECUTED
    assert c.state in ("completed", "closing")
    assert broker.list_positions() == []


def test_partial_fill_recorded(broker, store, registry, clock, nv_config):
    broker.inject = ["partial:0.5"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 1.0)
    assert c.leg(Side.BUY).intent_status is IntentStatus.PARTIALLY_FILLED
    assert c.leg(Side.BUY).filled_volume == 0.5
    assert c.state == "both_legs_open"
    assert "partial_fill" in store.kinds()
    assert any("volumes differ" in n for n in c.notes)


def test_initial_stop_missing_is_placed_after_fill(broker, store, registry, clock, nv_config):
    broker.inject = ["reject_sl"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert c.state == "both_legs_open"
    assert c.leg(Side.BUY).confirmed_sl == pytest.approx(1.09860)


def test_initial_stop_cannot_be_placed_closes_and_trips_breaker(broker, store, registry, clock, nv_config):
    broker.inject = ["reject_sl"]
    broker.inject_modify = ["reject", "reject", "reject"]
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert any(b[0] is BreakerType.STOP_PLACEMENT_FAILURE for b in store.breakers)
    assert c.leg(Side.BUY).status is LegStatus.CLOSED
    assert c.leg(Side.BUY).exit_reason == "unprotected_position"
    assert c.state == "completed"
    assert broker.list_positions() == []


def _stop_out_sell(market, broker, clock):
    clock.advance(30)
    market.set("EURUSD", 1.10150, 1.10160)  # ask >= SELL stop 1.10150
    broker.process_market()


def test_trailing_activates_only_after_confirmed_stop(broker, market, store, registry, clock, nv_config):
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    # Price moves toward the SELL stop but does not hit it -> no trailing
    clock.advance(10)
    market.set("EURUSD", 1.10140, 1.10149)
    broker.process_market()
    ex.manage(c)
    assert c.state == "both_legs_open" and c.trailing is None

    _stop_out_sell(market, broker, clock)
    ex.manage(c)
    assert c.leg(Side.SELL).status is LegStatus.CLOSED
    assert c.leg(Side.SELL).exit_reason == "stop_loss"
    assert c.state == "trailing_active"
    assert c.trailing["water_mark"] == pytest.approx(1.10150)
    # BUY trailing: SL = H_bid - 10 pips = 1.10050, above the initial 1.09860
    assert c.leg(Side.BUY).confirmed_sl == pytest.approx(1.10050)
    assert "trailing_activated" in store.kinds()


def test_trailing_moves_up_only_and_closes_with_trailing_stop(
    broker, market, store, registry, clock, nv_config
):
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    _stop_out_sell(market, broker, clock)
    ex.manage(c)
    clock.advance(5)
    market.set("EURUSD", 1.10300, 1.10310)
    ex.manage(c)
    assert c.leg(Side.BUY).confirmed_sl == pytest.approx(1.10200)
    # Pull back (not to the stop): stop must not move down
    clock.advance(5)
    market.set("EURUSD", 1.10250, 1.10260)
    ex.manage(c)
    assert c.leg(Side.BUY).confirmed_sl == pytest.approx(1.10200)
    # Hit the trailing stop
    clock.advance(5)
    market.set("EURUSD", 1.10195, 1.10205)
    broker.process_market()
    ex.manage(c)
    assert c.state == "completed"
    assert c.outcome == "trailed"
    assert c.leg(Side.BUY).exit_reason == "trailing_stop"
    total = c.leg(Side.BUY).net_pnl + c.leg(Side.SELL).net_pnl
    assert c.realized_pnl == pytest.approx(round(total, 2))


def test_trailing_modification_rejected_keeps_last_confirmed(
    broker, market, store, registry, clock, nv_config
):
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    _stop_out_sell(market, broker, clock)
    ex.manage(c)
    confirmed = c.leg(Side.BUY).confirmed_sl
    broker.inject_modify = ["reject"] * 20
    clock.advance(5)
    market.set("EURUSD", 1.10400, 1.10410)
    ex.manage(c)
    assert c.leg(Side.BUY).confirmed_sl == confirmed
    assert broker.list_positions()[0].sl == confirmed
    assert "stop_modify_failed" in store.kinds()
    # repeated failures trip the breaker
    for _ in range(4):
        clock.advance(120)
        market.set("EURUSD", 1.10400, 1.10410)  # keep the quote fresh
        ex.manage(c)
    assert any(b[0] is BreakerType.STOP_MODIFICATION_FAILURE for b in store.breakers)


def test_both_legs_stopped(broker, market, store, registry, clock, nv_config):
    nv_config.trailing.enabled = True
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    # Whipsaw between management ticks: both stops hit before trailing can act
    clock.advance(1)
    market.set("EURUSD", 1.10150, 1.10160)
    broker.process_market()
    market.set("EURUSD", 1.09850, 1.09860)
    broker.process_market()
    ex.manage(c)
    assert c.state == "completed"
    assert c.outcome == "both_stopped"
    assert c.trailing_activated_at is None
    assert c.realized_pnl < 0


def test_restart_with_open_position_resumes_trailing(broker, market, store, registry, clock, nv_config):
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    _stop_out_sell(market, broker, clock)
    ex.manage(c)
    sl_before = c.leg(Side.BUY).confirmed_sl
    # "Restart": new broker object from persisted state, new executor
    broker2 = SimulatedBroker(market, clock, broker.config, registry, state=broker.to_state())
    ex2 = make(broker2, store, registry, clock, nv_config)
    clock.advance(5)
    market.set("EURUSD", 1.10500, 1.10510)
    ex2.manage(c)
    assert c.leg(Side.BUY).confirmed_sl > sl_before
    assert len(broker2.list_positions()) == 1


def test_restart_mid_submission_reconciles_without_resubmitting(broker, store, registry, clock, nv_config):
    ex = make(broker, store, registry, clock, nv_config)
    c = new_cycle(clock)
    c = ex.open(c, plans(), 0.5)
    # Pretend the process died after sending but before recording results.
    c.state = "submitting_orders"
    for lg in c.legs:
        lg.intent_status = IntentStatus.SUBMITTED
        lg.status = LegStatus.PENDING
    n_orders = len(broker.orders)
    ex.manage(c)
    assert len(broker.orders) == n_orders  # nothing re-submitted
    assert c.state == "both_legs_open"


def test_duplicate_intent_does_not_duplicate_position(broker, store, registry, clock, nv_config):
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    leg = c.leg(Side.BUY)
    ex.submit_leg(c, leg)  # same client id again
    assert len(broker.list_positions()) == 2


def test_insufficient_margin_rejects(market, clock, registry, store, nv_config):
    b = SimulatedBroker(
        market,
        clock,
        SimConfig(initial_balance=100, leverage=30, slippage_points_max=0, latency_ms=0),
        registry,
    )
    ex = DualEntryExecutor(b, store, registry.get("EURUSD"), clock, GlobalRiskConfig(), nv_config)
    c = ex.open(new_cycle(clock), plans(), 5.0)
    assert c.state == "failed"
    assert c.leg(Side.BUY).reject_reason.startswith("insufficient_margin")


def test_netting_account_rejected(market, clock, registry, store, nv_config):
    b = SimulatedBroker(market, clock, SimConfig(margin_mode=MarginMode.NETTING), registry)
    ex = DualEntryExecutor(b, store, registry.get("EURUSD"), clock, GlobalRiskConfig(), nv_config)
    with pytest.raises(UnsupportedAccountMode):
        ex.open(new_cycle(clock), plans(), 0.1)
    assert b.list_positions() == []


def test_persistence_failure_before_submit_sends_nothing(broker, store, registry, clock, nv_config):
    store.fail_saves = True
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    assert broker.orders == {}
    assert "persistence failure" in c.error
    assert any(b[0] is BreakerType.PERSISTENCE_FAILURE for b in store.breakers)


def test_other_bot_cannot_touch_positions(broker, store, registry, clock, nv_config):
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock, magic=1001), plans(), 0.5)
    intruder = new_cycle(clock, bot_id="bot-daily", magic=2002, kind="single")
    intruder.legs = [c.leg(Side.BUY)]  # tries to reuse the other bot's leg
    with pytest.raises(OwnershipViolation):
        ex.close_leg(
            intruder,
            intruder.legs[0],
            reason=__import__("trading.interfaces.types", fromlist=["ExitReason"]).ExitReason.MANUAL,
        )
    assert len(broker.list_positions()) == 2


def test_max_duration_closes_survivor(broker, market, store, registry, clock, nv_config):
    nv_config.max_cycle_duration_minutes = 10
    ex = make(broker, store, registry, clock, nv_config)
    c = ex.open(new_cycle(clock), plans(), 0.5)
    clock.advance(11 * 60)
    ex.manage(c)
    assert c.state == "completed" and c.outcome == "max_duration"
    assert broker.list_positions() == []
