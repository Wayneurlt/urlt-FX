"""Dual-entry trading-cycle state machine."""

from __future__ import annotations

from enum import StrEnum


class CycleState(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    REJECTED = "rejected"
    SUBMITTING_ORDERS = "submitting_orders"
    ONE_LEG_FILLED = "one_leg_filled"
    BOTH_LEGS_OPEN = "both_legs_open"
    ONE_LEG_STOPPED = "one_leg_stopped"
    TRAILING_ACTIVE = "trailing_active"
    CLOSING = "closing"
    COMPLETED = "completed"
    FAILED = "failed"
    EMERGENCY_HALT = "emergency_halt"


S = CycleState

TRANSITIONS: dict[CycleState, frozenset[CycleState]] = {
    S.CREATED: frozenset({S.VALIDATING, S.REJECTED, S.FAILED}),
    S.VALIDATING: frozenset({S.SUBMITTING_ORDERS, S.REJECTED, S.FAILED}),
    S.SUBMITTING_ORDERS: frozenset(
        {S.BOTH_LEGS_OPEN, S.ONE_LEG_FILLED, S.FAILED, S.EMERGENCY_HALT, S.CLOSING}
    ),
    S.ONE_LEG_FILLED: frozenset(
        {S.CLOSING, S.ONE_LEG_STOPPED, S.TRAILING_ACTIVE, S.COMPLETED, S.EMERGENCY_HALT}
    ),
    S.BOTH_LEGS_OPEN: frozenset({S.ONE_LEG_STOPPED, S.CLOSING, S.COMPLETED, S.EMERGENCY_HALT}),
    S.ONE_LEG_STOPPED: frozenset({S.TRAILING_ACTIVE, S.CLOSING, S.COMPLETED, S.EMERGENCY_HALT}),
    S.TRAILING_ACTIVE: frozenset({S.CLOSING, S.COMPLETED, S.EMERGENCY_HALT}),
    S.CLOSING: frozenset({S.COMPLETED, S.EMERGENCY_HALT}),
    S.EMERGENCY_HALT: frozenset(
        {S.CLOSING, S.COMPLETED, S.BOTH_LEGS_OPEN, S.ONE_LEG_STOPPED, S.TRAILING_ACTIVE}
    ),
    S.COMPLETED: frozenset(),
    S.REJECTED: frozenset(),
    S.FAILED: frozenset(),
}

TERMINAL = frozenset({S.COMPLETED, S.REJECTED, S.FAILED})
ACTIVE = frozenset(set(CycleState) - TERMINAL - {S.CREATED, S.VALIDATING})


class InvalidTransition(RuntimeError):
    pass


def check_transition(current: CycleState, new: CycleState) -> None:
    if new not in TRANSITIONS[current]:
        raise InvalidTransition(f"cycle cannot move from {current.value} to {new.value}")
