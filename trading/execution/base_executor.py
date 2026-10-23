"""Operations shared by the dual-entry and single-trade executors.

Every broker operation that touches an existing position first verifies that
the position belongs to the calling cycle (magic number + client order id),
so one bot can never modify or close another bot's positions.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from trading.adapters.base import (
    BrokerAdapter,
    BrokerDisconnected,
    BrokerError,
    BrokerTimeout,
    DealEntry,
    DealReason,
    OrderRejected,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OwnershipViolation,
    Position,
    PositionNotFound,
    UnknownOrderOutcome,
)
from trading.execution.records import CycleRecord, ExecutionStore, IntentStatus, LegRecord, LegStatus
from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import ExitReason, Side
from trading.risk.circuit_breakers import BreakerScope, BreakerType
from trading.risk.config import GlobalRiskConfig


class PersistenceError(RuntimeError):
    pass


class BaseExecutor:
    RECONCILE_ATTEMPTS = 2

    def __init__(
        self,
        broker: BrokerAdapter,
        store: ExecutionStore,
        spec: InstrumentSpec,
        clock: Callable[[], datetime],
        risk: GlobalRiskConfig,
    ) -> None:
        self.broker = broker
        self.store = store
        self.spec = spec
        self.clock = clock
        self.risk = risk

    # --- persistence -------------------------------------------------------
    def persist(self, cycle: CycleRecord) -> None:
        cycle.updated_at = self.clock()
        try:
            self.store.save(cycle)
        except Exception as exc:  # noqa: BLE001 - any persistence failure is fatal for trading
            try:
                self.store.trip_breaker(
                    BreakerType.PERSISTENCE_FAILURE,
                    BreakerScope.GLOBAL,
                    None,
                    f"could not persist cycle {cycle.id}: {exc}",
                )
            except Exception:  # noqa: BLE001 - the store itself may be down; the original error is what matters
                pass
            raise PersistenceError(str(exc)) from exc

    # --- ownership ---------------------------------------------------------
    def owned_position(self, cycle: CycleRecord, leg: LegRecord) -> Position | None:
        """The broker position for ``leg`` if it is still open. Verifies ownership."""
        if leg.position_id is None:
            return None
        for p in self.broker.list_positions(symbol=self.spec.name_at_broker):
            if p.position_id == leg.position_id:
                self.assert_owned(cycle, leg, p)
                return p
        return None

    @staticmethod
    def assert_owned(cycle: CycleRecord, leg: LegRecord, p: Position) -> None:
        if p.magic != cycle.magic or p.client_order_id != leg.client_order_id:
            raise OwnershipViolation(
                f"position {p.position_id} (magic {p.magic}, client {p.client_order_id}) does not belong to "
                f"cycle {cycle.id} leg {leg.client_order_id} (magic {cycle.magic})"
            )

    # --- submission ----------------------------------------------------------
    def submit_leg(self, cycle: CycleRecord, leg: LegRecord) -> None:
        """Send one market order. The intent must already be persisted."""
        req = OrderRequest(
            client_order_id=leg.client_order_id,
            symbol=self.spec.name_at_broker,
            side=leg.side,
            volume=leg.requested_volume,
            sl=leg.intended_sl,
            tp=leg.intended_tp,
            magic=cycle.magic,
            comment=leg.client_order_id,
        )
        leg.intent_status = IntentStatus.SUBMITTED
        try:
            res = self.broker.submit_market_order(req)
            self.apply_fill(cycle, leg, res)
        except OrderRejected as exc:
            leg.intent_status = IntentStatus.REJECTED
            leg.status = LegStatus.NOT_OPENED
            leg.reject_reason = f"{exc.code}: {exc}"
            self.store.event(cycle, "entry_rejected", f"{leg.side.value} order rejected: {exc}", "warning")
        except (UnknownOrderOutcome, BrokerTimeout, BrokerDisconnected) as exc:
            leg.intent_status = IntentStatus.UNKNOWN
            self.store.event(
                cycle,
                "unknown_order_state",
                f"{leg.side.value} order outcome unknown ({exc.code}): {exc}",
                "critical",
            )
        except BrokerError as exc:
            leg.intent_status = IntentStatus.UNKNOWN
            self.store.event(
                cycle,
                "unknown_order_state",
                f"{leg.side.value} order failed with {exc.code}: {exc}",
                "critical",
            )

    def apply_fill(self, cycle: CycleRecord, leg: LegRecord, res: OrderResult) -> None:
        if res.status is OrderStatus.REJECTED:
            leg.intent_status = IntentStatus.REJECTED
            leg.status = LegStatus.NOT_OPENED
            leg.reject_reason = res.reject_reason
            return
        if res.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) or res.filled_volume <= 0:
            leg.intent_status = IntentStatus.UNKNOWN
            return
        leg.intent_status = (
            IntentStatus.FILLED if res.status is OrderStatus.FILLED else IntentStatus.PARTIALLY_FILLED
        )
        leg.status = LegStatus.OPEN
        leg.filled_volume = res.filled_volume
        leg.position_id = res.position_id
        leg.broker_order_id = res.broker_order_id
        leg.fill_price = res.fill_price
        leg.fill_time = res.time or self.clock()
        leg.commission += res.commission
        leg.confirmed_sl = res.sl
        if res.fill_price is not None:
            adverse = (
                (res.fill_price - leg.expected_price)
                if leg.side is Side.BUY
                else (leg.expected_price - res.fill_price)
            )
            leg.slippage_pips = round(self.spec.price_to_pips(adverse), 2)
        kind = "order_filled" if res.status is OrderStatus.FILLED else "partial_fill"
        self.store.event(
            cycle,
            kind,
            f"{leg.side.value} {res.filled_volume}/{leg.requested_volume} lots filled at {res.fill_price}",
            "info" if kind == "order_filled" else "warning",
        )
        if leg.slippage_pips is not None and leg.slippage_pips > self.risk.abnormal_slippage_pips:
            self.store.trip_breaker(
                BreakerType.ABNORMAL_SLIPPAGE,
                BreakerScope.BOT,
                cycle.bot_id,
                f"{leg.side.value} slippage {leg.slippage_pips} pips > {self.risk.abnormal_slippage_pips}",
            )

    # --- reconciliation ------------------------------------------------------
    def resolve_unknown(self, cycle: CycleRecord, leg: LegRecord) -> bool:
        """Ask the broker what happened to an order with unknown outcome.

        Returns True if resolved. Never re-submits.
        """
        absent = 0
        for _ in range(self.RECONCILE_ATTEMPTS + 1):
            try:
                look = self.broker.find_order(leg.client_order_id)
                if look.found and look.result is not None:
                    self.apply_fill(cycle, leg, look.result)
                    if leg.intent_status is not IntentStatus.UNKNOWN:
                        self.store.event(
                            cycle,
                            "order_reconciled",
                            f"{leg.side.value} order resolved as {leg.intent_status.value}",
                        )
                        return True
                    continue
                # Not in order history: also check positions by client id before concluding.
                pos = [
                    p
                    for p in self.broker.list_positions(symbol=self.spec.name_at_broker)
                    if p.client_order_id == leg.client_order_id
                ]
            except BrokerError:
                absent = 0
                continue
            if pos:
                p = pos[0]
                self.assert_owned(cycle, leg, p)
                self.apply_fill(
                    cycle,
                    leg,
                    OrderResult(
                        leg.client_order_id,
                        OrderStatus.FILLED,
                        None,
                        p.position_id,
                        leg.requested_volume,
                        p.volume,
                        p.open_price,
                        p.sl,
                        p.open_time,
                    ),
                )
                return True
            absent += 1
            if absent >= self.RECONCILE_ATTEMPTS:
                leg.intent_status = IntentStatus.NOT_EXECUTED
                leg.status = LegStatus.NOT_OPENED
                self.store.event(
                    cycle,
                    "order_reconciled",
                    f"{leg.side.value} order not found at broker after {absent} checks: treated as not executed",
                )
                return True
        return False

    # --- stops -------------------------------------------------------------
    def verify_stop(self, cycle: CycleRecord, leg: LegRecord) -> bool:
        """Ensure the leg has a broker-confirmed protective stop. Returns True if protected."""
        try:
            p = self.owned_position(cycle, leg)
        except BrokerError:
            return False
        if p is None:
            return True  # already closed: nothing to protect, sync will record it
        if p.sl is not None and leg.intended_sl is not None and self.spec.same_price(p.sl, leg.intended_sl):
            leg.confirmed_sl = p.sl
            leg.sl_verified_at = self.clock()
            leg.stop_history.append(
                {"time": self.clock().isoformat(), "sl": p.sl, "action": "initial_confirmed"}
            )
            return True
        if p.sl is not None and leg.intended_sl is None:
            leg.confirmed_sl = p.sl
            return True
        # Stop missing or different: try to (re)place it.
        target = leg.intended_sl
        if target is None:
            return False
        for _attempt in range(3):
            try:
                r = self.broker.modify_stop(p.position_id, target)
            except BrokerError:
                continue
            if r.accepted:
                leg.confirmed_sl = r.sl
                leg.sl_verified_at = self.clock()
                leg.stop_history.append(
                    {"time": self.clock().isoformat(), "sl": r.sl, "action": "initial_placed_after_fill"}
                )
                return True
            leg.stop_history.append(
                {"time": self.clock().isoformat(), "sl": target, "action": "rejected", "reason": r.reason}
            )
        leg.confirmed_sl = p.sl
        return False

    def handle_unprotected(self, cycle: CycleRecord, leg: LegRecord) -> None:
        self.store.trip_breaker(
            BreakerType.STOP_PLACEMENT_FAILURE,
            BreakerScope.BOT,
            cycle.bot_id,
            f"could not verify protective stop on {leg.side.value} {self.spec.symbol} (cycle {cycle.id})",
        )
        self.store.event(
            cycle,
            "stop_placement_failed",
            f"{leg.side.value} leg has no verified stop; closing it",
            "critical",
        )
        self.close_leg(cycle, leg, ExitReason.UNPROTECTED_POSITION)

    def modify_stop(self, cycle: CycleRecord, leg: LegRecord, new_sl: float, action: str) -> bool:
        p = self.owned_position(cycle, leg)
        if p is None:
            return False
        try:
            r = self.broker.modify_stop(p.position_id, new_sl)
        except BrokerError as exc:
            leg.stop_history.append(
                {"time": self.clock().isoformat(), "sl": new_sl, "action": "error", "reason": str(exc)}
            )
            return False
        if r.accepted:
            leg.stop_history.append(
                {"time": self.clock().isoformat(), "sl": r.sl, "previous": leg.confirmed_sl, "action": action}
            )
            leg.confirmed_sl = r.sl
            return True
        leg.stop_history.append(
            {"time": self.clock().isoformat(), "sl": new_sl, "action": "rejected", "reason": r.reason}
        )
        return False

    # --- closing & sync ------------------------------------------------------
    def close_leg(self, cycle: CycleRecord, leg: LegRecord, reason: ExitReason) -> bool:
        try:
            p = self.owned_position(cycle, leg)
        except OwnershipViolation:
            raise
        except BrokerError as exc:
            self.store.event(
                cycle, "close_failed", f"could not read position for {leg.side.value}: {exc}", "critical"
            )
            return False
        if p is None:
            return self.sync_leg(cycle, leg, None)
        try:
            res = self.broker.close_position(p.position_id, None, client_order_id=f"{leg.client_order_id}X")
        except BrokerError as exc:
            self.store.event(
                cycle, "close_failed", f"close of {leg.side.value} failed ({exc.code}): {exc}", "critical"
            )
            return False
        leg.exit_reason = reason.value
        self.store.event(
            cycle, "position_closed", f"{leg.side.value} closed ({reason.value}) at {res.fill_price}"
        )
        return self.sync_leg(cycle, leg, None, default_reason=reason)

    def sync_leg(
        self,
        cycle: CycleRecord,
        leg: LegRecord,
        positions: dict[str, Position] | None,
        default_reason: ExitReason | None = None,
    ) -> bool:
        """Refresh an OPEN leg from the broker. Returns True if the leg is now closed and fully recorded."""
        if leg.status is not LegStatus.OPEN:
            return leg.status is LegStatus.CLOSED
        if positions is None:
            positions = {
                p.position_id: p for p in self.broker.list_positions(symbol=self.spec.name_at_broker)
            }
        p = positions.get(leg.position_id or "")
        if p is not None:
            self.assert_owned(cycle, leg, p)
            leg.floating_pnl = p.profit
            if p.volume + 1e-9 < leg.filled_volume:
                cycle.note(
                    f"{leg.side.value} position volume reduced at broker: {leg.filled_volume} -> {p.volume}"
                )
                leg.filled_volume = p.volume
            if p.sl is None or (
                leg.confirmed_sl is not None and not self.spec.same_price(p.sl, leg.confirmed_sl)
            ):
                # The broker's stop is authoritative.
                leg.stop_history.append(
                    {
                        "time": self.clock().isoformat(),
                        "sl": p.sl,
                        "previous": leg.confirmed_sl,
                        "action": "broker_reported",
                    }
                )
                leg.confirmed_sl = p.sl
            return False
        # Position is gone: confirm with deal history.
        since = (leg.fill_time or cycle.created_at) - timedelta(minutes=1)
        deals = [
            d
            for d in self.broker.deal_history(since, position_id=leg.position_id)
            if d.entry is DealEntry.OUT
        ]
        if not deals:
            raise PositionNotFound(f"position {leg.position_id} disappeared without a closing deal")
        vol = sum(d.volume for d in deals)
        leg.exit_price = (
            self.spec.round_price(sum(d.price * d.volume for d in deals) / vol) if vol else deals[-1].price
        )
        leg.exit_time = deals[-1].time
        leg.gross_pnl = sum(d.profit for d in deals)
        leg.commission += sum(d.commission for d in deals)
        leg.swap = sum(d.swap for d in deals)
        leg.net_pnl = leg.gross_pnl + leg.commission + leg.swap
        leg.floating_pnl = 0.0
        last = deals[-1].reason
        if leg.exit_reason is None:
            if last is DealReason.STOP_LOSS:
                leg.exit_reason = ExitReason.STOP_LOSS.value
            elif last is DealReason.TAKE_PROFIT:
                leg.exit_reason = ExitReason.TAKE_PROFIT.value
            elif last is DealReason.STOP_OUT:
                leg.exit_reason = ExitReason.BROKER.value
            else:
                leg.exit_reason = (default_reason or ExitReason.UNKNOWN).value
        leg.status = LegStatus.CLOSED
        leg_closed_by_stop = last is DealReason.STOP_LOSS
        leg.stop_history.append(
            {
                "time": leg.exit_time.isoformat(),
                "action": "closed",
                "price": leg.exit_price,
                "deal_reason": last.value,
            }
        )
        if leg_closed_by_stop:
            self.store.event(
                cycle,
                "stop_loss_hit",
                f"{leg.side.value} stopped out at {leg.exit_price} (net {leg.net_pnl:.2f})",
                "warning",
            )
        return True

    def finalize(self, cycle: CycleRecord) -> None:
        cycle.realized_pnl = round(sum(lg.net_pnl for lg in cycle.legs), 2)
        cycle.closed_at = self.clock()
