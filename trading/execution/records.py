"""Execution records shared by both bots' executors and the persistence layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from trading.interfaces.types import Side
from trading.risk.circuit_breakers import BreakerScope, BreakerType


class IntentStatus(StrEnum):
    PENDING_SUBMIT = "pending_submit"  # persisted, not yet sent
    SUBMITTED = "submitted"  # sent, no answer yet
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"  # outcome not known: must be reconciled, never blindly retried
    NOT_EXECUTED = "not_executed"  # reconciled: broker has no record of execution


class LegStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    NOT_OPENED = "not_opened"


@dataclass
class LegRecord:
    id: str
    side: Side
    client_order_id: str
    requested_volume: float
    intended_sl: float | None
    expected_price: float
    intended_tp: float | None = None
    intent_status: IntentStatus = IntentStatus.PENDING_SUBMIT
    status: LegStatus = LegStatus.PENDING
    filled_volume: float = 0.0
    position_id: str | None = None
    broker_order_id: str | None = None
    fill_price: float | None = None
    fill_time: datetime | None = None
    confirmed_sl: float | None = None
    sl_verified_at: datetime | None = None
    tp: float | None = None
    exit_price: float | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    net_pnl: float = 0.0
    floating_pnl: float = 0.0
    slippage_pips: float | None = None
    reject_reason: str | None = None
    stop_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["intent_status"] = self.intent_status.value
        d["status"] = self.status.value
        for k in ("fill_time", "sl_verified_at", "exit_time"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass
class CycleRecord:
    """A dual-entry cycle (two legs) or a daily-bot trade (one leg)."""

    id: str
    kind: str  # "dual_entry" | "single"
    bot_id: str
    magic: int
    strategy_version: str
    trigger_type: str
    symbol: str
    environment: str
    state: str
    created_at: datetime
    updated_at: datetime
    signal_key: str
    event_id: str | None = None
    volume: float = 0.0
    planned_risk: float = 0.0
    risk_decision: dict[str, Any] = field(default_factory=dict)
    signal: dict[str, Any] = field(default_factory=dict)
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    legs: list[LegRecord] = field(default_factory=list)
    trailing: dict[str, Any] | None = None
    trailing_activated_at: datetime | None = None
    management: dict[str, Any] = field(default_factory=dict)  # break-even / trailing flags for single trades
    outcome: str | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    closed_at: datetime | None = None
    realized_pnl: float = 0.0
    simulated: bool = True

    def leg(self, side: Side) -> LegRecord | None:
        return next((lg for lg in self.legs if lg.side is side), None)

    def open_legs(self) -> list[LegRecord]:
        return [lg for lg in self.legs if lg.status is LegStatus.OPEN]

    def note(self, msg: str) -> None:
        self.notes.append(msg)


class ExecutionStore(Protocol):
    """Persistence + side effects used by executors. Implementations MUST raise
    if a save cannot be made durable, so the executor can stop before sending
    an order it could not record."""

    def save(self, cycle: CycleRecord) -> None: ...

    def event(
        self,
        cycle: CycleRecord,
        kind: str,
        message: str,
        severity: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None: ...

    def trip_breaker(
        self, type: BreakerType, scope: BreakerScope, scope_key: str | None, reason: str
    ) -> None: ...


class InMemoryStore:
    """Test/backtest store. Records every saved snapshot."""

    def __init__(self) -> None:
        self.cycles: dict[str, CycleRecord] = {}
        self.events: list[tuple[str, str, str, str]] = []
        self.breakers: list[tuple[BreakerType, BreakerScope, str | None, str]] = []
        self.saves = 0
        self.fail_saves = False
        self.state_history: dict[str, list[str]] = {}

    def save(self, cycle: CycleRecord) -> None:
        if self.fail_saves:
            raise RuntimeError("simulated persistence failure")
        self.saves += 1
        self.cycles[cycle.id] = cycle
        hist = self.state_history.setdefault(cycle.id, [])
        if not hist or hist[-1] != cycle.state:
            hist.append(cycle.state)

    def event(
        self,
        cycle: CycleRecord,
        kind: str,
        message: str,
        severity: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((cycle.id, kind, severity, message))

    def trip_breaker(
        self, type: BreakerType, scope: BreakerScope, scope_key: str | None, reason: str
    ) -> None:
        self.breakers.append((type, scope, scope_key, reason))

    def kinds(self) -> list[str]:
        return [e[1] for e in self.events]
