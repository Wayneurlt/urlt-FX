"""Serializers and shared helpers for API responses."""

from __future__ import annotations

from typing import Any

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.models import (
    AuditLog,
    Bot,
    CircuitBreakerRow,
    EconomicEventRow,
    Notification,
    PositionLeg,
    TradingCycle,
    TradingSignal,
)


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)) -> None:
        self.page = page
        self.page_size = page_size


def paginate(s: Session, q: Select, p: PageParams, mapper) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    total = s.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    rows = s.scalars(q.offset((p.page - 1) * p.page_size).limit(p.page_size)).all()
    return {"items": [mapper(r) for r in rows], "total": int(total), "page": p.page, "page_size": p.page_size}


def breaker_out(b: CircuitBreakerRow) -> dict[str, Any]:
    return {
        "id": b.id,
        "type": b.type,
        "scope": b.scope,
        "scope_key": b.scope_key,
        "reason": b.reason,
        "critical": b.critical,
        "auto_clear": b.auto_clear,
        "recovery": b.recovery,
        "activated_at": b.activated_at,
        "cleared_at": b.cleared_at,
        "cleared_by": b.cleared_by,
        "clear_note": b.clear_note,
        "active": b.active,
        "environment": b.environment,
    }


def notification_out(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "created_at": n.created_at,
        "severity": n.severity,
        "kind": n.kind,
        "title": n.title,
        "message": n.message,
        "bot_id": n.bot_id,
        "cycle_id": n.cycle_id,
        "read": n.read_at is not None,
    }


def log_out(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "created_at": a.created_at,
        "severity": a.severity,
        "category": a.category,
        "message": a.message,
        "correlation_id": a.correlation_id,
        "bot_id": a.bot_id,
        "cycle_id": a.cycle_id,
        "actor": a.actor,
        "environment": a.environment,
        "data": a.data,
    }


def event_out(s: Session, e: EconomicEventRow) -> dict[str, Any]:
    processed = (
        s.scalar(select(func.count()).select_from(TradingSignal).where(TradingSignal.event_id == e.event_id))
        or 0
    )
    return {
        "id": e.id,
        "event_id": e.event_id,
        "currency": e.currency,
        "name": e.name,
        "scheduled_at": e.scheduled_at,
        "impact": e.impact,
        "forecast": e.forecast,
        "previous": e.previous,
        "actual": e.actual,
        "provider": e.provider,
        "origin": e.origin,
        "status": e.status,
        "processed": bool(processed),
        "fetched_at": e.fetched_at,
    }


def leg_out(lg: PositionLeg) -> dict[str, Any]:
    return {
        "id": lg.id,
        "side": lg.side,
        "client_order_id": lg.client_order_id,
        "intent_status": lg.intent_status,
        "status": lg.status,
        "requested_volume": lg.requested_volume,
        "filled_volume": lg.filled_volume,
        "position_id": lg.position_id,
        "broker_order_id": lg.broker_order_id,
        "expected_price": lg.expected_price,
        "fill_price": lg.fill_price,
        "fill_time": lg.fill_time,
        "intended_sl": lg.intended_sl,
        "confirmed_sl": lg.confirmed_sl,
        "sl_verified_at": lg.sl_verified_at,
        "tp": lg.tp or lg.intended_tp,
        "exit_price": lg.exit_price,
        "exit_time": lg.exit_time,
        "exit_reason": lg.exit_reason,
        "gross_pnl": lg.gross_pnl,
        "commission": lg.commission,
        "swap": lg.swap,
        "net_pnl": lg.net_pnl,
        "floating_pnl": lg.floating_pnl,
        "slippage_pips": lg.slippage_pips,
        "reject_reason": lg.reject_reason,
        "stop_history": lg.stop_history or [],
    }


def _bot_names(s: Session) -> dict[str, str]:
    return {b.id: b.name for b in s.scalars(select(Bot))}


def cycle_out(c: TradingCycle, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": c.id,
        "kind": c.kind,
        "bot_id": c.bot_id,
        "bot_name": (names or {}).get(c.bot_id, c.bot_id),
        "strategy_version": c.strategy_version,
        "trigger_type": c.trigger_type,
        "event_id": c.event_id,
        "symbol": c.symbol,
        "environment": c.environment,
        "state": c.state,
        "active": c.active,
        "volume": c.volume,
        "planned_risk": c.planned_risk,
        "realized_pnl": c.realized_pnl,
        "outcome": c.outcome,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "closed_at": c.closed_at,
        "trailing_activated_at": c.trailing_activated_at,
        "simulated": c.simulated,
        "legs": [leg_out(lg) for lg in c.legs],
        "notes": c.notes or [],
        "error": c.error,
    }


def trade_row_out(lg: PositionLeg, names: dict[str, str]) -> dict[str, Any]:
    c = lg.cycle
    return {
        **leg_out(lg),
        "cycle_id": c.id,
        "bot_id": c.bot_id,
        "bot_name": names.get(c.bot_id, c.bot_id),
        "symbol": c.symbol,
        "strategy_version": c.strategy_version,
        "trigger_type": c.trigger_type,
        "environment": c.environment,
        "cycle_state": c.state,
        "simulated": c.simulated,
        "cycle_kind": c.kind,
    }


bot_names = _bot_names
