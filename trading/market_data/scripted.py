"""Scripted market data for tests: quotes are set explicitly by the test."""

from __future__ import annotations

from datetime import datetime

from trading.instruments.conversion import RateBook
from trading.interfaces.types import DataSource
from trading.market_data.models import Candle, Quote
from trading.market_data.provider import MarketStatus


class ScriptedMarketData:
    source = DataSource.SCRIPTED
    name = "scripted"

    def __init__(
        self,
        clock,
        quotes: dict[str, tuple[float, float]] | None = None,
        rates: dict[str, float] | None = None,
    ) -> None:
        self.clock = clock
        self._q: dict[str, tuple[float, float]] = dict(quotes or {})
        self._quote_time: dict[str, datetime] = {}
        self._rates = dict(rates or {})
        self.candles: dict[tuple[str, str], list[Candle]] = {}
        self.open = True

    def set(self, symbol: str, bid: float, ask: float, at: datetime | None = None) -> None:
        self._q[symbol] = (bid, ask)
        self._quote_time[symbol] = at or self.clock()

    def get_quote(self, symbol: str, now: datetime) -> Quote:
        bid, ask = self._q[symbol]
        t = self._quote_time.get(symbol)
        return Quote(symbol, bid, ask, t if t is not None else now, DataSource.SCRIPTED)

    def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        return [c for c in self.candles.get((symbol, timeframe), []) if start <= c.time < end]

    def market_status(self, symbol: str, now: datetime) -> MarketStatus:
        return MarketStatus(self.open, "" if self.open else "closed by test")

    def rates(self, now: datetime) -> RateBook:
        rb = RateBook(self._rates)
        for s, (b, a) in self._q.items():
            if len(s) == 6:
                rb.set(s, (b + a) / 2)
        return rb
