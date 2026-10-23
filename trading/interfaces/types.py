"""Shared enums and small value types used across the trading domain.

Nothing in this module talks to a database, a network or a broker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(ts: datetime) -> datetime:
    """Reject naive datetimes; normalise aware ones to UTC."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime is not allowed; all timestamps must be timezone-aware UTC")
    return ts.astimezone(UTC)


class Environment(StrEnum):
    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"


class DataSource(StrEnum):
    """Where a price came from. Always shown alongside the data."""

    SYNTHETIC = "synthetic"  # generated locally, not real market data
    HISTORICAL = "historical"  # imported historical file
    DELAYED = "delayed"
    LIVE = "live"
    SCRIPTED = "scripted"  # test fixtures


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class BotKind(StrEnum):
    NEWS_VOLATILITY = "news_volatility"
    DAILY_TRADING = "daily_trading"


class BotStatus(StrEnum):
    DISABLED = "disabled"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    RISK_HALTED = "risk_halted"
    ERROR = "error"


class TriggerType(StrEnum):
    NEWS = "news"
    VOLATILITY = "volatility"
    DAILY_SIGNAL = "daily_signal"
    MANUAL = "manual"


class MarginMode(StrEnum):
    HEDGING = "hedging"
    NETTING = "netting"
    UNKNOWN = "unknown"


class ExitReason(StrEnum):
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TAKE_PROFIT = "take_profit"
    MANUAL = "manual"
    UNMATCHED_EXPOSURE = "unmatched_exposure"
    UNPROTECTED_POSITION = "unprotected_position"
    MAX_DURATION = "max_duration"
    SESSION_CUTOFF = "session_cutoff"
    LIQUIDATION = "liquidation"
    END_OF_DATA = "end_of_data"
    BROKER = "broker"
    UNKNOWN = "unknown"
