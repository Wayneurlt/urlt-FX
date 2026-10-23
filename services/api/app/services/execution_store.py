"""Database implementation of ``ExecutionStore`` + ORM <-> record mapping.

``save`` commits immediately: the executor relies on it being durable before
it sends an order.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Deal, OrderIntent, PositionLeg, StopModification, TradingCycle
from app.services import risk_service
from app.services.audit import NOTIFY_KINDS, audit, notify
from trading.execution.records import CycleRecord, IntentStatus, LegRecord, LegStatus
from trading.interfaces.types import Environment, Side
from trading.risk.circuit_breakers import BreakerScope, BreakerType

TERMINAL_STATES = {"completed", "rejected", "failed", "closed"}

# Event kinds that are also surfaced as notifications (others are audit-only).
_NOTIFY = {
    "entry_rejected",
    "order_filled",
    "partial_fill",
    "stop_loss_hit",
    "trailing_activated",
    "position_closed",
    "unknown_order_state",
    "stop_modify_failed",
    "stop_placement_failed",
    "unmatched_exposure",
}


def _dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(v)


def record_from_row(row: TradingCycle) -> CycleRecord:
    legs = []
    for lg in row.legs:
        legs.append(
            LegRecord(
                id=lg.id,
                side=Side(lg.side),
                client_order_id=lg.client_order_id,
                requested_volume=lg.requested_volume,
                intended_sl=lg.intended_sl,
                expected_price=lg.expected_price or 0.0,
                intended_tp=lg.intended_tp,
                intent_status=IntentStatus(lg.intent_status),
                status=LegStatus(lg.status),
                filled_volume=lg.filled_volume,
                position_id=lg.position_id,
                broker_order_id=lg.broker_order_id,
                fill_price=lg.fill_price,
                fill_time=lg.fill_time,
                confirmed_sl=lg.confirmed_sl,
                sl_verified_at=lg.sl_verified_at,
                tp=lg.tp,
                exit_price=lg.exit_price,
                exit_time=lg.exit_time,
                exit_reason=lg.exit_reason,
                gross_pnl=lg.gross_pnl,
                commission=lg.commission,
                swap=lg.swap,
                net_pnl=lg.net_pnl,
                floating_pnl=lg.floating_pnl,
                slippage_pips=lg.slippage_pips,
                reject_reason=lg.reject_reason,
                stop_history=list(lg.stop_history or []),
            )
        )
    return CycleRecord(
        id=row.id,
        kind=row.kind,
        bot_id=row.bot_id,
        magic=row.magic,
        strategy_version=row.strategy_version,
        trigger_type=row.trigger_type,
        symbol=row.symbol,
        environment=row.environment,
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
        signal_key=row.signal_key,
        event_id=row.event_id,
        volume=row.volume,
        planned_risk=row.planned_risk,
        risk_decision=dict(row.risk_decision or {}),
        signal=dict(row.signal or {}),
        config_snapshot=dict(row.config_snapshot or {}),
        legs=legs,
        trailing=dict(row.trailing) if row.trailing else None,
        trailing_activated_at=row.trailing_activated_at,
        management=dict(row.management or {}),
        outcome=row.outcome,
        notes=list(row.notes or []),
        error=row.error,
        closed_at=row.closed_at,
        realized_pnl=row.realized_pnl,
        simulated=row.simulated,
    )


class DbExecutionStore:
    def __init__(self, s: Session, environment: Environment, actor: str = "system") -> None:
        self.s = s
        self.env = environment
        self.actor = actor

    def save(self, c: CycleRecord) -> None:
        try:
            self._save(c)
        except Exception:
            self.s.rollback()
            raise

    def _save(self, c: CycleRecord) -> None:
        s = self.s
        row = s.get(TradingCycle, c.id)
        if row is None:
            row = TradingCycle(id=c.id)
            s.add(row)
        row.kind, row.bot_id, row.magic, row.strategy_version = c.kind, c.bot_id, c.magic, c.strategy_version
        row.trigger_type, row.event_id, row.signal_key, row.symbol = (
            c.trigger_type,
            c.event_id,
            c.signal_key,
            c.symbol,
        )
        row.environment, row.state, row.volume, row.planned_risk = (
            c.environment,
            c.state,
            c.volume,
            c.planned_risk,
        )
        row.realized_pnl, row.outcome, row.risk_decision, row.signal = (
            c.realized_pnl,
            c.outcome,
            c.risk_decision,
            c.signal,
        )
        row.config_snapshot, row.trailing, row.management = c.config_snapshot, c.trailing, dict(c.management)
        row.notes, row.error, row.simulated = list(c.notes), c.error, c.simulated
        row.created_at, row.updated_at, row.closed_at = c.created_at, c.updated_at or utcnow(), c.closed_at
        row.trailing_activated_at = c.trailing_activated_at
        row.active = c.state not in TERMINAL_STATES
        s.flush()
        for lg in c.legs:
            self._save_leg(c, lg)
        if not row.active:
            risk_service.release_reservation(s, c.id)
        s.commit()

    def _save_leg(self, c: CycleRecord, lg: LegRecord) -> None:
        s = self.s
        pl = s.get(PositionLeg, lg.id)
        if pl is None:
            pl = PositionLeg(id=lg.id, cycle_id=c.id)
            s.add(pl)
        for f in (
            "client_order_id",
            "requested_volume",
            "filled_volume",
            "position_id",
            "broker_order_id",
            "expected_price",
            "fill_price",
            "fill_time",
            "intended_sl",
            "confirmed_sl",
            "sl_verified_at",
            "intended_tp",
            "tp",
            "exit_price",
            "exit_time",
            "exit_reason",
            "gross_pnl",
            "commission",
            "swap",
            "net_pnl",
            "floating_pnl",
            "slippage_pips",
            "reject_reason",
        ):
            setattr(pl, f, getattr(lg, f))
        pl.side, pl.status, pl.intent_status = lg.side.value, lg.status.value, lg.intent_status.value
        pl.stop_history = list(lg.stop_history)

        oi = s.get(OrderIntent, lg.id)
        if oi is None:
            oi = OrderIntent(
                id=lg.id,
                cycle_id=c.id,
                client_order_id=lg.client_order_id,
                symbol=c.symbol,
                side=lg.side.value,
                requested_volume=lg.requested_volume,
                intent_status=lg.intent_status.value,
                simulated=c.simulated,
            )
            s.add(oi)
        oi.intent_status, oi.filled_volume, oi.broker_order_id = (
            lg.intent_status.value,
            lg.filled_volume,
            lg.broker_order_id,
        )
        oi.fill_price, oi.reject_reason, oi.updated_at = lg.fill_price, lg.reject_reason, utcnow()

        # Append-only stop modification history
        existing = {
            m.seq for m in s.scalars(select(StopModification).where(StopModification.leg_id == lg.id))
        }
        for i, h in enumerate(lg.stop_history):
            if i in existing:
                continue
            s.add(
                StopModification(
                    cycle_id=c.id,
                    leg_id=lg.id,
                    seq=i,
                    time=_dt(h.get("time")) or utcnow(),
                    action=h.get("action", ""),
                    sl=h.get("sl"),
                    previous=h.get("previous"),
                    reason=h.get("reason"),
                )
            )
        # Deals (entry / exit)
        if (
            lg.fill_price is not None
            and lg.filled_volume > 0
            and s.scalars(select(Deal.id).where(Deal.leg_id == lg.id, Deal.entry == "in")).first() is None
        ):
            s.add(
                Deal(
                    cycle_id=c.id,
                    leg_id=lg.id,
                    entry="in",
                    side=lg.side.value,
                    volume=lg.filled_volume,
                    price=lg.fill_price,
                    time=lg.fill_time or utcnow(),
                    reason="entry",
                    simulated=c.simulated,
                )
            )
        if (
            lg.status is LegStatus.CLOSED
            and lg.exit_price is not None
            and s.scalars(select(Deal.id).where(Deal.leg_id == lg.id, Deal.entry == "out")).first() is None
        ):
            s.add(
                Deal(
                    cycle_id=c.id,
                    leg_id=lg.id,
                    entry="out",
                    side=lg.side.opposite.value,
                    volume=lg.filled_volume,
                    price=lg.exit_price,
                    time=lg.exit_time or utcnow(),
                    reason=lg.exit_reason or "",
                    profit=lg.gross_pnl,
                    commission=lg.commission,
                    swap=lg.swap,
                    simulated=c.simulated,
                )
            )

    def event(
        self,
        c: CycleRecord,
        kind: str,
        message: str,
        severity: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        category = (
            "state_transition"
            if kind in ("cycle_state", "trade_state")
            else ("stop_modification" if "stop" in kind or "trailing" in kind else "execution")
        )
        audit(
            self.s,
            category,
            f"[{c.symbol} {c.id[:8]}] {message}",
            severity,
            bot_id=c.bot_id,
            cycle_id=c.id,
            actor=self.actor,
            environment=self.env.value,
            data=data or {"kind": kind},
        )
        if kind in _NOTIFY:
            notify(
                self.s,
                kind,
                f"{c.symbol}: {message}",
                severity,
                title=NOTIFY_KINDS.get(kind),
                bot_id=c.bot_id,
                cycle_id=c.id,
            )
        if (
            kind == "cycle_state"
            and data
            and data.get("to") == "completed"
            or kind == "trade_state"
            and data
            and data.get("to") == "closed"
        ):
            notify(
                self.s,
                "position_closed",
                f"{c.symbol} {c.kind.replace('_', ' ')} finished: {c.outcome}, net {c.realized_pnl:.2f}",
                "info",
                bot_id=c.bot_id,
                cycle_id=c.id,
            )
        self.s.commit()

    def trip_breaker(
        self, type: BreakerType, scope: BreakerScope, scope_key: str | None, reason: str
    ) -> None:
        risk_service.trip_breaker(self.s, self.env, type, scope, scope_key, reason)
        self.s.commit()


def load_active_cycles(s: Session, env: Environment, bot_id: str | None = None) -> list[TradingCycle]:
    q = select(TradingCycle).where(TradingCycle.active.is_(True), TradingCycle.environment == env.value)
    if bot_id:
        q = q.where(TradingCycle.bot_id == bot_id)
    return list(s.scalars(q.order_by(TradingCycle.created_at)))
