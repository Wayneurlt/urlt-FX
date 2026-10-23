"""Bot lifecycle state machine.

Semantics:
* ``pause``  - no new entries; existing positions keep being managed.
* ``stop``   - no new entries; existing positions keep being managed (never
               silently liquidated). Orderly shutdown with liquidation is a
               separate, explicitly confirmed operation.
* ``start``  - moves to STARTING; the worker runs pre-start checks (broker
               connection, account capabilities, instrument specs) and moves
               the bot to RUNNING or ERROR.
* Open positions of every bot are managed by the worker regardless of the
  bot's status, so no status leaves exposure unmanaged.
"""

from __future__ import annotations

from trading.interfaces.types import BotStatus

B = BotStatus

ACTIONS: dict[str, dict[BotStatus, BotStatus]] = {
    "enable": {B.DISABLED: B.STOPPED},
    "disable": {B.STOPPED: B.DISABLED, B.ERROR: B.DISABLED},
    "start": {B.STOPPED: B.STARTING, B.ERROR: B.STARTING},
    "pause": {B.RUNNING: B.PAUSED},
    "resume": {B.PAUSED: B.RUNNING, B.RISK_HALTED: B.RUNNING},
    "stop": {
        B.RUNNING: B.STOPPED,
        B.PAUSED: B.STOPPED,
        B.STARTING: B.STOPPED,
        B.RISK_HALTED: B.STOPPED,
        B.ERROR: B.STOPPED,
    },
}

# Transitions performed by the system (worker), not by users.
SYSTEM_TRANSITIONS: dict[BotStatus, frozenset[BotStatus]] = {
    B.STARTING: frozenset({B.RUNNING, B.ERROR}),
    B.RUNNING: frozenset({B.RISK_HALTED, B.ERROR}),
    B.PAUSED: frozenset({B.RISK_HALTED, B.ERROR}),
    B.RISK_HALTED: frozenset({B.ERROR}),
}


class InvalidBotTransition(ValueError):
    pass


def next_status(current: BotStatus, action: str) -> BotStatus:
    table = ACTIONS.get(action)
    if table is None:
        raise InvalidBotTransition(f"unknown action {action!r}")
    if current not in table:
        raise InvalidBotTransition(f"cannot {action} a bot that is {current.value}")
    return table[current]


def allowed_actions(current: BotStatus) -> list[str]:
    return [a for a, t in ACTIONS.items() if current in t]


def can_accept_entries(status: BotStatus) -> bool:
    return status is B.RUNNING
