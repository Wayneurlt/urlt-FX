"""Broker adapter interface.

Strategy and execution code depend only on this module. Concrete adapters
(simulator, MT5, ...) live next to it and are never imported by strategies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import MarginMode, Side
from trading.market_data.models import Candle, Quote
from trading.market_data.provider import MarketStatus

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BrokerError(Exception):
    code = "broker_error"

    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        super().__init__(message or self.code)
        self.details = details or {}


class OrderRejected(BrokerError):
    code = "order_rejected"


class BrokerTimeout(BrokerError):
    code = "timeout"


class UnknownOrderOutcome(BrokerError):
    """The request may or may not have executed. Never blindly retry."""

    code = "unknown_order_outcome"


class BrokerDisconnected(BrokerError):
    code = "disconnected"


class InvalidVolume(OrderRejected):
    code = "invalid_volume"


class InvalidStop(OrderRejected):
    code = "invalid_stop"


class InsufficientMargin(OrderRejected):
    code = "insufficient_margin"


class UnsupportedAccountMode(BrokerError):
    code = "unsupported_account_mode"


class InstrumentUnavailable(BrokerError):
    code = "instrument_unavailable"


class BrokerRateLimited(BrokerError):
    code = "rate_limited"


class ReconciliationFailure(BrokerError):
    code = "reconciliation_failure"


class PositionNotFound(BrokerError):
    code = "position_not_found"


class OwnershipViolation(BrokerError):
    """A bot attempted to touch a position that belongs to another bot/cycle."""

    code = "ownership_violation"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class DealEntry(StrEnum):
    IN = "in"
    OUT = "out"


class DealReason(StrEnum):
    CLIENT = "client"  # closed by our explicit request
    STOP_LOSS = "sl"
    TAKE_PROFIT = "tp"
    STOP_OUT = "stop_out"  # margin call liquidation
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    connected: bool
    detail: str = ""
    checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccountInfo:
    account_id: str
    currency: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float | None
    leverage: float
    margin_mode: MarginMode
    is_simulated: bool
    is_demo: bool
    server: str = ""


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str  # idempotency key, unique per intent
    symbol: str
    side: Side
    volume: float
    sl: float | None
    tp: float | None = None
    magic: int = 0  # bot identifier at the broker
    comment: str = ""
    max_slippage_points: int = 20


@dataclass(frozen=True, slots=True)
class OrderResult:
    client_order_id: str
    status: OrderStatus
    broker_order_id: str | None = None
    position_id: str | None = None
    requested_volume: float = 0.0
    filled_volume: float = 0.0
    fill_price: float | None = None
    sl: float | None = None  # broker-confirmed SL on the resulting position
    time: datetime | None = None
    reject_reason: str | None = None
    commission: float = 0.0
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class Position:
    position_id: str
    symbol: str
    side: Side
    volume: float
    open_price: float
    open_time: datetime
    sl: float | None
    tp: float | None
    magic: int
    client_order_id: str
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class Deal:
    deal_id: str
    position_id: str
    order_id: str | None
    symbol: str
    side: Side
    volume: float
    price: float
    time: datetime
    entry: DealEntry
    reason: DealReason
    profit: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    magic: int = 0
    client_order_id: str = ""
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class OrderLookup:
    """Authoritative answer to "what happened to client order X?"."""

    client_order_id: str
    found: bool
    result: OrderResult | None = None


@dataclass(frozen=True, slots=True)
class StopModifyResult:
    position_id: str
    accepted: bool
    sl: float | None  # broker-confirmed stop after the request
    reason: str | None = None


@dataclass
class Capabilities:
    hedging: bool
    broker_side_stops: bool
    partial_fills_possible: bool
    client_order_ids: bool
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class BrokerAdapter(ABC):
    name: str = "abstract"
    is_simulated: bool = False

    @abstractmethod
    def connect(self) -> ConnectionStatus: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def check_connection(self) -> ConnectionStatus: ...

    @abstractmethod
    def account_info(self) -> AccountInfo: ...

    def trading_allowed(self) -> bool:
        """Whether the broker connection currently accepts new orders (e.g. MT5's Algo Trading switch)."""
        return True

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def instrument_spec(self, symbol: str) -> InstrumentSpec: ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]: ...

    @abstractmethod
    def market_status(self, symbol: str) -> MarketStatus: ...

    @abstractmethod
    def estimate_margin(self, symbol: str, side: Side, volume: float, price: float) -> float | None: ...

    @abstractmethod
    def submit_market_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def modify_stop(self, position_id: str, sl: float, tp: float | None = None) -> StopModifyResult: ...

    @abstractmethod
    def close_position(
        self, position_id: str, volume: float | None = None, client_order_id: str = ""
    ) -> OrderResult: ...

    @abstractmethod
    def list_positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]: ...

    @abstractmethod
    def list_orders(self) -> list[OrderResult]: ...

    @abstractmethod
    def order_history(self, since: datetime) -> list[OrderResult]: ...

    @abstractmethod
    def deal_history(self, since: datetime, position_id: str | None = None) -> list[Deal]: ...

    @abstractmethod
    def find_order(self, client_order_id: str) -> OrderLookup:
        """Look up an order by idempotency key in the authoritative store."""
        ...
