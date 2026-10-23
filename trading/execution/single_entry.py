"""Single-position executor for the Daily Trading bot."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from trading.adapters.base import BrokerAdapter, BrokerError, OwnershipViolation, PositionNotFound
from trading.execution.base_executor import BaseExecutor, PersistenceError
from trading.execution.records import CycleRecord, ExecutionStore, IntentStatus, LegRecord, LegStatus
from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import ExitReason, Side
from trading.market_data.quality import check_quotes_freshness
from trading.risk.circuit_breakers import BreakerScope, BreakerType
from trading.risk.config import GlobalRiskConfig
from trading.risk.sizing import LegPlan
from trading.strategies.daily_trading.config import DailyTradingConfig
from trading.strategies.daily_trading.trade import ACTIVE, TradeState, check_transition, past_close_time
from trading.strategies.news_volatility import trailing as tr

T = TradeState


class SingleTradeExecutor(BaseExecutor):
    def __init__(
        self,
        broker: BrokerAdapter,
        store: ExecutionStore,
        spec: InstrumentSpec,
        clock: Callable[[], datetime],
        risk: GlobalRiskConfig,
        config: DailyTradingConfig,
    ) -> None:
        super().__init__(broker, store, spec, clock, risk)
        self.config = config

    def to(self, trade: CycleRecord, new: TradeState, msg: str | None = None, severity: str = "info") -> None:
        check_transition(TradeState(trade.state), new)
        old = trade.state
        trade.state = new.value
        self.persist(trade)
        self.store.event(
            trade, "trade_state", msg or f"{old} -> {new.value}", severity, {"from": old, "to": new.value}
        )

    def open(self, trade: CycleRecord, plan: LegPlan, volume: float, tp: float | None) -> CycleRecord:
        short = trade.id.replace("-", "")[:12]
        trade.volume = volume
        leg = LegRecord(
            id=f"{trade.id}:{plan.side.value}",
            side=plan.side,
            client_order_id=f"dt{short}{plan.side.value[0]}",
            requested_volume=volume,
            intended_sl=self.spec.round_price(plan.stop),
            expected_price=plan.entry,
            intended_tp=tp,
        )
        trade.legs = [leg]
        trade.management = {
            "initial_risk_distance": abs(plan.entry - plan.stop),
            "break_even_done": False,
            "trailing": None,
        }
        try:
            self.to(trade, T.SUBMITTING, f"submitting {plan.side.value} {volume} lots on {trade.symbol}")
        except PersistenceError:
            trade.error = "persistence failure before submission; no order sent"
            return trade
        self.submit_leg(trade, leg)
        self.persist(trade)
        if leg.intent_status is IntentStatus.UNKNOWN and not self.resolve_unknown(trade, leg):
            self.store.trip_breaker(
                BreakerType.UNCERTAIN_ORDER_STATE,
                BreakerScope.BOT,
                trade.bot_id,
                f"trade {trade.id}: order outcome unknown",
            )
            self.to(trade, T.EMERGENCY_HALT, "order state unknown; new entries blocked", "critical")
            return trade
        if leg.status is not LegStatus.OPEN:
            trade.outcome = "not_filled"
            self.to(
                trade,
                T.FAILED,
                f"order not filled: {leg.reject_reason or leg.intent_status.value}",
                "warning",
            )
            return trade
        if not self.verify_stop(trade, leg):
            self.to(trade, T.CLOSING, "protective stop could not be verified", "critical")
            self.handle_unprotected(trade, leg)
            self._close_if_flat(trade, "stop_failure")
            return trade
        self.to(
            trade, T.OPEN, f"{plan.side.value} open at {leg.fill_price}, stop {leg.confirmed_sl}, target {tp}"
        )
        return trade

    def manage(self, trade: CycleRecord) -> CycleRecord:
        state = TradeState(trade.state)
        if state not in ACTIVE:
            return trade
        leg = trade.legs[0] if trade.legs else None
        if leg is None:
            return trade
        try:
            if state in (T.SUBMITTING, T.EMERGENCY_HALT):
                if leg.intent_status in (
                    IntentStatus.PENDING_SUBMIT,
                    IntentStatus.SUBMITTED,
                    IntentStatus.UNKNOWN,
                ):
                    if not self.resolve_unknown(trade, leg):
                        if state is T.SUBMITTING:
                            self.store.trip_breaker(
                                BreakerType.UNCERTAIN_ORDER_STATE,
                                BreakerScope.BOT,
                                trade.bot_id,
                                f"trade {trade.id} interrupted during submission",
                            )
                            self.to(trade, T.EMERGENCY_HALT, "order state unknown", "critical")
                        return trade
                if leg.status is LegStatus.OPEN:
                    if leg.confirmed_sl is None and not self.verify_stop(trade, leg):
                        self.to(trade, T.CLOSING)
                        self.handle_unprotected(trade, leg)
                        self._close_if_flat(trade, "stop_failure")
                        return trade
                    self.to(trade, T.OPEN, "reconciled: position open")
                else:
                    trade.outcome = "not_filled"
                    self.to(trade, T.FAILED, "reconciled: order was not executed")
                    return trade

            if leg.status is LegStatus.OPEN:
                self.sync_leg(trade, leg, None)
            if leg.status is LegStatus.CLOSED:
                self._close_if_flat(trade, leg.exit_reason or "closed")
                return trade

            if TradeState(trade.state) is T.CLOSING:
                self.close_leg(trade, leg, ExitReason.LIQUIDATION)
                self._close_if_flat(trade, "liquidated")
                return trade

            now = self.clock()
            opened = leg.fill_time or trade.created_at
            if now - opened > timedelta(minutes=self.config.max_holding_minutes):
                self._exit(trade, leg, ExitReason.MAX_DURATION)
                return trade
            if past_close_time(now, self.config, opened):
                self._exit(trade, leg, ExitReason.SESSION_CUTOFF)
                return trade
            self._manage_stop(trade, leg)
            self.persist(trade)
        except OwnershipViolation:
            raise
        except PositionNotFound as exc:
            self.store.trip_breaker(
                BreakerType.RECONCILIATION_FAILURE, BreakerScope.BOT, trade.bot_id, str(exc)
            )
            if TradeState(trade.state) is not T.EMERGENCY_HALT:
                self.to(trade, T.EMERGENCY_HALT, f"reconciliation failure: {exc}", "critical")
        except BrokerError as exc:
            self.store.event(trade, "broker_error", f"management tick failed ({exc.code}): {exc}", "warning")
        return trade

    def _manage_stop(self, trade: CycleRecord, leg: LegRecord) -> None:
        q = self.broker.get_quote(self.spec.name_at_broker)
        if check_quotes_freshness(q.time, self.clock(), self.risk.max_quote_age_seconds):
            return
        m = trade.management
        r_dist = m.get("initial_risk_distance") or 0.0
        if not r_dist or leg.fill_price is None:
            return
        progress = (q.bid - leg.fill_price) if leg.side is Side.BUY else (leg.fill_price - q.ask)
        r_mult = progress / r_dist
        be = self.config.break_even
        if be.enabled and not m.get("break_even_done") and r_mult >= be.trigger_r:
            off = self.spec.pips_to_price(be.offset_pips)
            target = leg.fill_price + off if leg.side is Side.BUY else leg.fill_price - off
            target = self.spec.round_price(target)
            if tr.is_protective_improvement(leg.side, leg.confirmed_sl, target, self.spec):
                if self.modify_stop(trade, leg, target, "break_even"):
                    m["break_even_done"] = True
                    self.store.event(trade, "break_even", f"stop moved to break-even {target}")
            else:
                m["break_even_done"] = True
        tc = self.config.trailing
        if tc.enabled and r_mult >= tc.activate_r:
            if m.get("trailing") is None:
                dist = self.spec.round_price(float(trade.signal.get("atr", r_dist)) * tc.distance_atr_mult)
                st = tr.activate(leg.side, q, dist, leg.confirmed_sl, self.clock().isoformat())
                m["trailing"] = st.to_dict()
                self.store.event(
                    trade,
                    "trailing_activated",
                    f"trailing activated, distance {self.spec.price_to_pips(dist):.1f} pips",
                )
            st = tr.TrailingState.from_dict(m["trailing"])
            st.confirmed_sl = leg.confirmed_sl
            cand = tr.next_stop(st, q, self.spec)
            if cand is not None and not self.modify_stop(trade, leg, cand, "trailing_update"):
                self.store.event(
                    trade,
                    "stop_modify_failed",
                    f"trailing update to {cand} rejected; keeping {leg.confirmed_sl}",
                    "warning",
                )
            st.confirmed_sl = leg.confirmed_sl
            m["trailing"] = st.to_dict()

    def _exit(self, trade: CycleRecord, leg: LegRecord, reason: ExitReason) -> None:
        self.to(trade, T.CLOSING, f"closing: {reason.value}")
        self.close_leg(trade, leg, reason)
        self._close_if_flat(trade, reason.value)

    def liquidate(self, trade: CycleRecord, reason: ExitReason = ExitReason.LIQUIDATION) -> CycleRecord:
        if TradeState(trade.state) not in ACTIVE:
            return trade
        leg = trade.legs[0]
        if TradeState(trade.state) is not T.CLOSING:
            self.to(trade, T.CLOSING, f"liquidation requested ({reason.value})", "warning")
        if leg.status is LegStatus.OPEN:
            self.close_leg(trade, leg, reason)
        self._close_if_flat(trade, "liquidated")
        return trade

    def _close_if_flat(self, trade: CycleRecord, outcome: str) -> None:
        if trade.open_legs():
            self.persist(trade)
            return
        trade.outcome = outcome
        self.finalize(trade)
        self.to(trade, T.CLOSED, f"trade closed ({outcome}); net {trade.realized_pnl:.2f}")
