"""Audit log + in-app notifications."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import correlation_id, redact
from app.db.base import utcnow
from app.models import AuditLog, Notification

log = logging.getLogger("audit")

# Event kinds that produce an in-app notification (title, severity override)
NOTIFY_KINDS: dict[str, str] = {
    "bot_started": "Bot started",
    "bot_stopped": "Bot stopped",
    "bot_paused": "Bot paused",
    "bot_error": "Bot error",
    "cycle_created": "Trading cycle created",
    "entry_rejected": "Entry rejected",
    "order_filled": "Order filled",
    "partial_fill": "Partial fill",
    "stop_loss_hit": "Stop-loss hit",
    "trailing_activated": "Trailing stop activated",
    "position_closed": "Position closed",
    "risk_limit_reached": "Risk limit reached",
    "circuit_breaker": "Circuit breaker activated",
    "broker_disconnected": "Broker disconnected",
    "unknown_order_state": "Unknown order state",
    "stop_modify_failed": "Protective stop modification failed",
    "stop_placement_failed": "Protective stop placement failed",
    "unmatched_exposure": "Unmatched exposure",
    "daily_summary": "Daily summary",
    "critical_error": "Critical system error",
    "liquidation": "Liquidation",
}


def audit(
    s: Session,
    category: str,
    message: str,
    severity: str = "info",
    *,
    bot_id: str | None = None,
    cycle_id: str | None = None,
    actor: str = "system",
    environment: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        severity=severity,
        category=category,
        message=redact(message)[:4000],
        correlation_id=correlation_id.get(),
        bot_id=bot_id,
        cycle_id=cycle_id,
        actor=actor,
        environment=environment,
        data=redact(data or {}),
    )
    s.add(row)
    level = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}.get(severity, 20)
    log.log(
        level,
        f"[{category}] {message}",
        extra={"data": {"bot_id": bot_id, "cycle_id": cycle_id, "actor": actor}},
    )
    return row


def notify(
    s: Session,
    kind: str,
    message: str,
    severity: str = "info",
    *,
    title: str | None = None,
    bot_id: str | None = None,
    cycle_id: str | None = None,
    dedupe_key: str | None = None,
    dedupe_window: timedelta | None = None,
) -> Notification | None:
    """Create an in-app notification, suppressing duplicates.

    * ``dedupe_window``: skip if an identical (kind, bot, cycle, message) notification
      exists within the window (retries / repeated polling).
    * ``dedupe_key``: permanent unique key (e.g. one daily summary per day).
    """
    if dedupe_window is not None:
        recent = s.scalars(
            select(Notification.id)
            .where(
                Notification.kind == kind,
                Notification.created_at >= utcnow() - dedupe_window,
                Notification.bot_id.is_(None) if bot_id is None else Notification.bot_id == bot_id,
                Notification.cycle_id.is_(None) if cycle_id is None else Notification.cycle_id == cycle_id,
                Notification.message == redact(message)[:2000],
            )
            .limit(1)
        ).first()
        if recent:
            return None
    n = Notification(
        severity=severity,
        kind=kind,
        title=title or NOTIFY_KINDS.get(kind, kind.replace("_", " ").title()),
        message=redact(message)[:2000],
        bot_id=bot_id,
        cycle_id=cycle_id,
        dedupe_key=dedupe_key,
    )
    if dedupe_key is None:
        s.add(n)
        return n
    try:
        with s.begin_nested():
            s.add(n)
            s.flush()
    except IntegrityError:
        return None
    return n
