"""Dual BUY + SELL entry executor for the News & Volatility bot.

Lifecycle (see ``CycleState``):

  VALIDATING -> SUBMITTING_ORDERS -> (both filled & protected)  BOTH_LEGS_OPEN
                                  -> (one filled)               ONE_LEG_FILLED -> policy
                                  -> (none filled)              FAILED
                                  -> (outcome unknown)          EMERGENCY_HALT
  BOTH_LEGS_OPEN -> (one leg confirmed stopped by a broker deal) ONE_LEG_STOPPED -> TRAILING_ACTIVE
  TRAILING_ACTIVE -> (survivor closed)                           COMPLETED

The executor persists the cycle after every consequential step. Order intents
are persisted BEFORE submission; if that save fails, nothing is sent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from trading.adapters.base import (
    BrokerAdapter,
    BrokerError,
    OwnershipViolation,
    PositionNotFound,
    UnsupportedAccountMode,
)
from trading.execution.base_executor import BaseExecutor, PersistenceError
from trading.execution.records import CycleRecord, ExecutionStore, IntentStatus, LegRecord, LegStatus
from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import ExitReason
from trading.market_data.quality import check_quotes_freshness
from trading.risk.circuit_breakers import BreakerScope, BreakerType
from trading.risk.config import GlobalRiskConfig
from trading.risk.sizing import LegPlan
from trading.strategies.news_volatility import trailing as tr
from trading.strategies.news_volatility.config import NewsVolatilityConfig
from trading.strategies.news_volatility.cycle import ACTIVE, CycleState, check_transition

S = CycleState


class DualEntryExecutor(BaseExecutor):
    def __init__(
        self,
        broker: BrokerAdapter,
        store: ExecutionStore,
        spec: InstrumentSpec,
        clock: Callable[[], datetime],
        risk: GlobalRiskConfig,
        config: NewsVolatilityConfig,
    ) -> None:
        super().__init__(broker, store, spec, clock, risk)
        self.config = config

    # --- state ---------------------------------------------------------------
    def to(self, cycle: CycleRecord, new: CycleState, msg: str | None = None, severity: str = "info") -> None:
        check_transition(CycleState(cycle.state), new)
        old = cycle.state
        cycle.state = new.value
        self.persist(cycle)
        self.store.event(
            cycle, "cycle_state", msg or f"{old} -> {new.value}", severity, {"from": old, "to": new.value}
        )

    # --- entry ---------------------------------------------------------------
    def open(self, cycle: CycleRecord, plans: list[LegPlan], volume: float) -> CycleRecord:
        """Submit both legs for an already risk-approved cycle in VALIDATING state."""
        if not self.broker.capabilities().hedging:
            cycle.error = "account does not support independent opposing positions (netting account)"
            self.to(cycle, S.REJECTED, cycle.error, "warning")
            raise UnsupportedAccountMode(cycle.error)

        short = cycle.id.replace("-", "")[:12]
        cycle.volume = volume
        cycle.legs = [
            LegRecord(
                id=f"{cycle.id}:{p.side.value}",
                side=p.side,
                client_order_id=f"nv{short}{p.side.value[0]}",
                requested_volume=volume,
                intended_sl=self.spec.round_price(p.stop),
                expected_price=p.entry,
            )
            for p in plans
        ]
        # Durable order intents BEFORE anything is sent.
        try:
            self.to(cycle, S.SUBMITTING_ORDERS, f"submitting BUY+SELL {volume} lots on {cycle.symbol}")
        except PersistenceError:
            cycle.error = "persistence failure before submission; no orders sent"
            return cycle

        for leg in cycle.legs:  # submitted back-to-back
            self.submit_leg(cycle, leg)
        self.persist(cycle)

        for leg in cycle.legs:
            if leg.intent_status is IntentStatus.UNKNOWN:
                self.resolve_unknown(cycle, leg)
        self.persist(cycle)

        if any(lg.intent_status is IntentStatus.UNKNOWN for lg in cycle.legs):
            self.store.trip_breaker(
                BreakerType.UNCERTAIN_ORDER_STATE,
                BreakerScope.BOT,
                cycle.bot_id,
                f"cycle {cycle.id}: order outcome could not be established",
            )
            self.to(
                cycle,
                S.EMERGENCY_HALT,
                "order state unknown after reconciliation; new entries blocked",
                "critical",
            )
            return cycle

        # Every filled leg must have a verified protective stop.
        for leg in cycle.open_legs():
            if not self.verify_stop(cycle, leg):
                self.handle_unprotected(cycle, leg)
        self.persist(cycle)

        filled = [
            lg
            for lg in cycle.legs
            if lg.status in (LegStatus.OPEN, LegStatus.CLOSED) and lg.filled_volume > 0
        ]
        open_legs = cycle.open_legs()
        if not filled:
            cycle.outcome = "no_fill"
            self.to(cycle, S.FAILED, "neither leg was filled", "warning")
            return cycle
        if len(filled) == 2 and len(open_legs) == 2:
            if abs(open_legs[0].filled_volume - open_legs[1].filled_volume) > 1e-9:
                vols = " / ".join(f"{lg.side.value} {lg.filled_volume}" for lg in open_legs)
                cycle.note(f"leg volumes differ after partial fill: {vols}")
            self.to(cycle, S.BOTH_LEGS_OPEN, "both legs open with verified stops")
            return cycle
        if len(filled) == 2 and len(open_legs) < 2:
            # A leg was closed because its stop could not be verified.
            self.to(cycle, S.CLOSING, "closing cycle after protective-stop failure", "critical")
            for leg in cycle.open_legs():
                self.close_leg(cycle, leg, ExitReason.UNPROTECTED_POSITION)
            self._complete_if_flat(cycle, "stop_failure")
            return cycle

        # Exactly one leg filled: unmatched exposure.
        self.to(cycle, S.ONE_LEG_FILLED, "only one leg filled: unmatched exposure", "warning")
        self.store.event(
            cycle,
            "unmatched_exposure",
            f"only {filled[0].side.value} filled; policy={self.risk.unmatched_leg_policy}",
            "warning",
        )
        if not open_legs:
            self._complete_if_flat(cycle, "unmatched_closed")
            return cycle
        if self.risk.unmatched_leg_policy == "close":
            self.to(cycle, S.CLOSING, "closing unmatched leg per policy")
            for leg in cycle.open_legs():
                self.close_leg(cycle, leg, ExitReason.UNMATCHED_EXPOSURE)
            self.persist(cycle)
            self._complete_if_flat(cycle, "unmatched_closed")
        return cycle

    # --- management ----------------------------------------------------------
    def manage(self, cycle: CycleRecord) -> CycleRecord:
        """One management tick for an active cycle."""
        state = CycleState(cycle.state)
        if state not in ACTIVE:
            return cycle
        try:
            if state is S.EMERGENCY_HALT:
                return self._recover_from_halt(cycle)
            if state is S.SUBMITTING_ORDERS:
                return self._recover_submitting(cycle)

            positions = {
                p.position_id: p for p in self.broker.list_positions(symbol=self.spec.name_at_broker)
            }
            for leg in cycle.open_legs():
                self.sync_leg(cycle, leg, positions)
                if (
                    state is S.TRAILING_ACTIVE
                    and leg.status is LegStatus.CLOSED
                    and cycle.trailing
                    and leg.side.value == cycle.trailing.get("side")
                    and leg.exit_reason == ExitReason.STOP_LOSS.value
                ):
                    leg.exit_reason = ExitReason.TRAILING_STOP.value
            self.persist(cycle)

            state = CycleState(cycle.state)
            if state is S.BOTH_LEGS_OPEN:
                self._after_both_open(cycle)
            elif state is S.ONE_LEG_FILLED:
                self._complete_if_flat(cycle, "single_leg_closed")
            elif state in (S.ONE_LEG_STOPPED, S.TRAILING_ACTIVE):
                if not cycle.open_legs():
                    self._complete_if_flat(cycle, self._outcome(cycle))
                elif state is S.TRAILING_ACTIVE:
                    self._trail(cycle)
            elif state is S.CLOSING:
                for leg in cycle.open_legs():
                    self.close_leg(cycle, leg, ExitReason.LIQUIDATION)
                self._complete_if_flat(cycle, cycle.outcome or "closed")

            if CycleState(cycle.state) in ACTIVE and cycle.open_legs() and self._expired(cycle):
                self.store.event(
                    cycle, "max_duration", "maximum cycle duration reached; closing remaining exposure"
                )
                if CycleState(cycle.state) is not S.CLOSING:
                    self.to(cycle, S.CLOSING)
                for leg in cycle.open_legs():
                    self.close_leg(cycle, leg, ExitReason.MAX_DURATION)
                self._complete_if_flat(cycle, "max_duration")
        except OwnershipViolation:
            raise
        except PositionNotFound as exc:
            self.store.trip_breaker(
                BreakerType.RECONCILIATION_FAILURE, BreakerScope.BOT, cycle.bot_id, str(exc)
            )
            cycle.error = str(exc)
            if CycleState(cycle.state) is not S.EMERGENCY_HALT:
                self.to(cycle, S.EMERGENCY_HALT, f"reconciliation failure: {exc}", "critical")
        except BrokerError as exc:
            self.store.event(cycle, "broker_error", f"management tick failed ({exc.code}): {exc}", "warning")
        return cycle

    def _after_both_open(self, cycle: CycleRecord) -> None:
        closed = [lg for lg in cycle.legs if lg.status is LegStatus.CLOSED]
        open_ = cycle.open_legs()
        if not closed:
            return
        if not open_:
            self.to(cycle, S.ONE_LEG_STOPPED, "both legs closed before trailing could start", "warning")
            self._complete_if_flat(cycle, self._outcome(cycle))
            return
        stopped = closed[0]
        survivor = open_[0]
        if stopped.exit_reason != ExitReason.STOP_LOSS.value:
            cycle.note(
                f"{stopped.side.value} leg closed by {stopped.exit_reason}, not a stop-out: trailing NOT activated"
            )
            self.to(
                cycle,
                S.ONE_LEG_STOPPED,
                f"{stopped.side.value} leg closed ({stopped.exit_reason}); trailing not activated",
                "warning",
            )
            return
        self.to(
            cycle, S.ONE_LEG_STOPPED, f"{stopped.side.value} leg confirmed stopped at {stopped.exit_price}"
        )
        if not self.config.trailing.enabled:
            cycle.note("trailing disabled: survivor keeps its initial stop")
            return
        self._activate_trailing(cycle, survivor)

    def _activate_trailing(self, cycle: CycleRecord, survivor: LegRecord) -> None:
        p = self.owned_position(cycle, survivor)
        if p is None:
            self.sync_leg(cycle, survivor, None)
            self._complete_if_flat(cycle, self._outcome(cycle))
            return
        survivor.filled_volume = p.volume
        survivor.confirmed_sl = p.sl
        q = self.broker.get_quote(self.spec.name_at_broker)
        stale = check_quotes_freshness(q.time, self.clock(), self.risk.max_quote_age_seconds)
        if stale:
            self.store.event(cycle, "trailing_deferred", f"trailing activation deferred: {stale}", "warning")
            return
        dist = self.spec.pips_to_price(self.config.trailing_pips(cycle.symbol))
        state = tr.activate(survivor.side, q, dist, p.sl, self.clock().isoformat())
        cycle.trailing = state.to_dict()
        cycle.trailing_activated_at = self.clock()
        self.to(
            cycle,
            S.TRAILING_ACTIVE,
            f"trailing stop activated on {survivor.side.value}: distance {self.config.trailing_pips(cycle.symbol)} pips, reference {state.water_mark}",
        )
        self.store.event(
            cycle, "trailing_activated", f"trailing activated on surviving {survivor.side.value} leg"
        )
        self._trail(cycle)

    def _trail(self, cycle: CycleRecord) -> None:
        if cycle.trailing is None:
            return
        survivor = cycle.open_legs()[0] if cycle.open_legs() else None
        if survivor is None:
            return
        state = tr.TrailingState.from_dict(cycle.trailing)
        state.confirmed_sl = survivor.confirmed_sl
        now = self.clock()
        next_try = cycle.trailing.get("next_attempt_at")
        if next_try and datetime.fromisoformat(next_try) > now:
            return
        q = self.broker.get_quote(self.spec.name_at_broker)
        stale = check_quotes_freshness(q.time, now, self.risk.max_quote_age_seconds)
        if stale:
            cycle.trailing = {**state.to_dict(), "last_skip": stale}
            self.persist(cycle)
            return
        min_step = self.spec.pips_to_price(self.config.trailing.min_step_pips)
        cand = tr.next_stop(state, q, self.spec, min_step)
        if cand is not None:
            ok = False
            for _ in range(max(1, self.config.trailing.max_modify_retries)):
                if self.modify_stop(cycle, survivor, cand, "trailing_update"):
                    ok = True
                    break
            if ok:
                state.confirmed_sl = survivor.confirmed_sl
                state.failures = 0
            else:
                state.failures += 1
                backoff = min(60, 2**state.failures)
                extra = {"next_attempt_at": (now + timedelta(seconds=backoff)).isoformat()}
                self.store.event(
                    cycle,
                    "stop_modify_failed",
                    f"trailing stop update to {cand} rejected; last confirmed stop {survivor.confirmed_sl} remains authoritative",
                    "warning",
                )
                if state.failures >= self.config.trailing.max_modify_retries:
                    self.store.trip_breaker(
                        BreakerType.STOP_MODIFICATION_FAILURE,
                        BreakerScope.BOT,
                        cycle.bot_id,
                        f"{state.failures} consecutive trailing-stop failures on cycle {cycle.id}",
                    )
                cycle.trailing = {**state.to_dict(), **extra}
                if survivor.confirmed_sl is None:
                    self.handle_unprotected(cycle, survivor)
                self.persist(cycle)
                return
        cycle.trailing = state.to_dict()
        self.persist(cycle)

    # --- recovery ------------------------------------------------------------
    def _recover_submitting(self, cycle: CycleRecord) -> CycleRecord:
        """Process restarted mid-submission: reconcile, never re-submit."""
        for leg in cycle.legs:
            if leg.intent_status in (
                IntentStatus.PENDING_SUBMIT,
                IntentStatus.SUBMITTED,
                IntentStatus.UNKNOWN,
            ):
                if not self.resolve_unknown(cycle, leg):
                    leg.intent_status = IntentStatus.UNKNOWN
        self.persist(cycle)
        if any(lg.intent_status is IntentStatus.UNKNOWN for lg in cycle.legs):
            self.store.trip_breaker(
                BreakerType.UNCERTAIN_ORDER_STATE,
                BreakerScope.BOT,
                cycle.bot_id,
                f"cycle {cycle.id} interrupted during submission",
            )
            self.to(cycle, S.EMERGENCY_HALT, "interrupted during submission; order state unknown", "critical")
            return cycle
        for leg in cycle.open_legs():
            if not self.verify_stop(cycle, leg):
                self.handle_unprotected(cycle, leg)
        opened = cycle.open_legs()
        if len(opened) == 2:
            self.to(cycle, S.BOTH_LEGS_OPEN, "recovered after restart: both legs open")
        elif len(opened) == 1:
            self.to(cycle, S.ONE_LEG_FILLED, "recovered after restart: one leg open", "warning")
            if self.risk.unmatched_leg_policy == "close":
                self.to(cycle, S.CLOSING)
                self.close_leg(cycle, opened[0], ExitReason.UNMATCHED_EXPOSURE)
                self._complete_if_flat(cycle, "unmatched_closed")
        else:
            filled_then_closed = [lg for lg in cycle.legs if lg.status is LegStatus.CLOSED]
            if filled_then_closed:
                self.to(cycle, S.CLOSING)
                self._complete_if_flat(cycle, "closed_during_recovery")
            else:
                cycle.outcome = "no_fill"
                self.to(cycle, S.FAILED, "recovered after restart: no leg was filled")
        return cycle

    def _recover_from_halt(self, cycle: CycleRecord) -> CycleRecord:
        unresolved = [
            lg
            for lg in cycle.legs
            if lg.intent_status in (IntentStatus.UNKNOWN, IntentStatus.SUBMITTED, IntentStatus.PENDING_SUBMIT)
        ]
        for leg in unresolved:
            self.resolve_unknown(cycle, leg)
        if any(
            lg.intent_status in (IntentStatus.UNKNOWN, IntentStatus.SUBMITTED, IntentStatus.PENDING_SUBMIT)
            for lg in cycle.legs
        ):
            self.persist(cycle)
            return cycle
        # Everything is known again. Keep managing what exists; the breaker stays until cleared manually.
        try:
            positions = {
                p.position_id: p for p in self.broker.list_positions(symbol=self.spec.name_at_broker)
            }
            for leg in cycle.open_legs():
                self.sync_leg(cycle, leg, positions)
        except PositionNotFound:
            self.persist(cycle)
            return cycle
        for leg in cycle.open_legs():
            if leg.confirmed_sl is None and not self.verify_stop(cycle, leg):
                self.handle_unprotected(cycle, leg)
        opened = cycle.open_legs()
        if not opened:
            self.to(cycle, S.CLOSING, "reconciled: no open exposure")
            self._complete_if_flat(cycle, self._outcome(cycle))
        elif len(opened) == 2:
            self.to(cycle, S.BOTH_LEGS_OPEN, "reconciled: both legs open")
        elif cycle.trailing:
            self.to(cycle, S.TRAILING_ACTIVE, "reconciled: resuming trailing on survivor")
        else:
            self.to(
                cycle,
                S.CLOSING,
                "reconciled: one unmatched leg; closing per policy"
                if self.risk.unmatched_leg_policy == "close"
                else "reconciled: one leg open",
            )
            if self.risk.unmatched_leg_policy == "close":
                for leg in opened:
                    self.close_leg(cycle, leg, ExitReason.UNMATCHED_EXPOSURE)
            self._complete_if_flat(cycle, self._outcome(cycle))
        return cycle

    # --- liquidation -------------------------------------------------------
    def liquidate(self, cycle: CycleRecord, reason: ExitReason = ExitReason.LIQUIDATION) -> CycleRecord:
        """Authorised close of all of this cycle's open legs."""
        if CycleState(cycle.state) not in ACTIVE:
            return cycle
        if CycleState(cycle.state) is not S.CLOSING:
            self.to(cycle, S.CLOSING, f"liquidation requested ({reason.value})", "warning")
        for leg in cycle.open_legs():
            self.close_leg(cycle, leg, reason)
        self._complete_if_flat(cycle, "liquidated")
        return cycle

    # --- helpers -------------------------------------------------------------
    def _expired(self, cycle: CycleRecord) -> bool:
        return self.clock() - cycle.created_at > timedelta(minutes=self.config.max_cycle_duration_minutes)

    def _outcome(self, cycle: CycleRecord) -> str:
        reasons = [lg.exit_reason for lg in cycle.legs if lg.status is LegStatus.CLOSED]
        if cycle.trailing_activated_at is None and reasons.count(ExitReason.STOP_LOSS.value) == 2:
            return "both_stopped"
        if cycle.trailing_activated_at is not None:
            return "trailed"
        return cycle.outcome or "closed"

    def _complete_if_flat(self, cycle: CycleRecord, outcome: str) -> None:
        if cycle.open_legs():
            self.persist(cycle)
            return
        cycle.outcome = cycle.outcome if cycle.outcome and outcome == "closed" else outcome
        self.finalize(cycle)
        self.to(cycle, S.COMPLETED, f"cycle completed ({cycle.outcome}); net {cycle.realized_pnl:.2f}")
