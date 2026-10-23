from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading.interfaces.types import DataSource


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    time: datetime  # UTC
    source: DataSource

    def __post_init__(self) -> None:
        if self.time.tzinfo is None:
            raise ValueError("Quote.time must be timezone-aware")
        if not (self.bid > 0 and self.ask > 0):
            raise ValueError("bid/ask must be positive")
        if self.ask < self.bid:
            raise ValueError(f"crossed quote for {self.symbol}: bid={self.bid} ask={self.ask}")

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    def age(self, now: datetime) -> timedelta:
        return now - self.time

    def is_fresh(self, now: datetime, max_age_seconds: float) -> bool:
        age = self.age(now).total_seconds()
        return -1.0 <= age <= max_age_seconds


@dataclass(frozen=True, slots=True)
class Candle:
    """A bid-price candle plus the spread (ask = bid + spread).

    ``time`` is the candle OPEN time in UTC. A candle is only "closed" once
    ``time + timeframe`` has passed.
    """

    time: datetime
    open: float
    high: float
    low: float
    close: float
    spread: float = 0.0
    volume: float = 0.0
    complete: bool = True


TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


def timeframe_delta(tf: str) -> timedelta:
    try:
        return timedelta(minutes=TIMEFRAME_MINUTES[tf])
    except KeyError as exc:
        raise ValueError(f"unsupported timeframe {tf!r}") from exc
