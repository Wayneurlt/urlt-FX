"""Daily-bot trade state machine (independent of the dual-entry cycle machine)."""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum

from trading.strategies.daily_trading.config import DailyTradingConfig


class TradeState(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    REJECTED = "rejected"
    SUBMITTING = "submitting"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"
    EMERGENCY_HALT = "emergency_halt"


T = TradeState
TRANSITIONS: dict[TradeState, frozenset[TradeState]] = {
    T.CREATED: frozenset({T.VALIDATING, T.REJECTED, T.FAILED}),
    T.VALIDATING: frozenset({T.SUBMITTING, T.REJECTED, T.FAILED}),
    T.SUBMITTING: frozenset({T.OPEN, T.FAILED, T.EMERGENCY_HALT, T.CLOSING}),
    T.OPEN: frozenset({T.CLOSING, T.CLOSED, T.EMERGENCY_HALT}),
    T.CLOSING: frozenset({T.CLOSED, T.EMERGENCY_HALT}),
    T.EMERGENCY_HALT: frozenset({T.OPEN, T.CLOSING, T.CLOSED, T.FAILED}),
    T.CLOSED: frozenset(),
    T.REJECTED: frozenset(),
    T.FAILED: frozenset(),
}
TERMINAL = frozenset({T.CLOSED, T.REJECTED, T.FAILED})
ACTIVE = frozenset({T.SUBMITTING, T.OPEN, T.CLOSING, T.EMERGENCY_HALT})


class InvalidTradeTransition(RuntimeError):
    pass


def check_transition(cur: TradeState, new: TradeState) -> None:
    if new not in TRANSITIONS[cur]:
        raise InvalidTradeTransition(f"trade cannot move from {cur.value} to {new.value}")


def in_entry_session(now: datetime, cfg: DailyTradingConfig) -> tuple[bool, str]:
    if now.weekday() not in cfg.session.weekdays:
        return False, "not a configured trading day"
    t = now.time().replace(tzinfo=None)
    if t < cfg.session.start:
        return False, f"before session start {cfg.session.start}"
    if t >= cfg.session.end:
        return False, f"after entry cutoff {cfg.session.end}"
    return True, "in session"


def past_close_time(now: datetime, cfg: DailyTradingConfig, opened_at: datetime) -> bool:
    ct: time | None = cfg.session.close_time
    if ct is None:
        return False
    if now.date() > opened_at.date():
        return True
    return now.time().replace(tzinfo=None) >= ct
