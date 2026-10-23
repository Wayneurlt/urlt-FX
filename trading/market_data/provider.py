from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from trading.instruments.conversion import RateBook
from trading.interfaces.types import DataSource
from trading.market_data.models import Candle, Quote


class MarketDataUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MarketStatus:
    open: bool
    reason: str = ""


def fx_market_open(now: datetime) -> MarketStatus:
    """Approximate spot-FX session: Sunday 22:00 UTC to Friday 22:00 UTC.

    Broker-reported trading sessions take precedence when a broker is connected.
    """
    now = now.astimezone(UTC)
    wd, hour = now.weekday(), now.hour
    if wd == 5:
        return MarketStatus(False, "weekend (Saturday)")
    if wd == 4 and hour >= 22:
        return MarketStatus(False, "weekend (after Friday 22:00 UTC)")
    if wd == 6 and hour < 22:
        return MarketStatus(False, "weekend (before Sunday 22:00 UTC)")
    return MarketStatus(True)


@runtime_checkable
class MarketDataProvider(Protocol):
    source: DataSource
    name: str

    def get_quote(self, symbol: str, now: datetime) -> Quote: ...

    def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        """Closed candles with open time in [start, end)."""
        ...

    def market_status(self, symbol: str, now: datetime) -> MarketStatus: ...

    def rates(self, now: datetime) -> RateBook: ...


def closed_candles_before(candles: list[Candle], now: datetime, tf_delta: timedelta) -> list[Candle]:
    """Drop any candle that has not finished at ``now`` (prevents look-ahead)."""
    return [c for c in candles if c.time + tf_delta <= now]
