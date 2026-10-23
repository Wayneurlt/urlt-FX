"""Strategy interfaces.

A strategy only *proposes*: it turns closed market data into signals and leg
plans. It never sizes positions, never talks to a broker and never bypasses
the risk engine. The same strategy objects are used by the live/paper
runtime and by the backtester.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import Side, TriggerType
from trading.market_data.models import Candle, Quote
from trading.risk.sizing import LegPlan


@dataclass
class StrategySignal:
    symbol: str
    trigger: TriggerType
    dedupe_key: str
    candle_time: datetime | None
    side: Side | None = None  # None => dual-entry (both sides)
    stop_distance: float | None = None  # price units
    target_distance: float | None = None
    event_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalEvaluation:
    """Result of evaluating one instrument: a signal or the reason there is none."""

    signal: StrategySignal | None
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


class DailyStrategy(ABC):
    """Interface for intraday single-direction strategies used by the Daily bot."""

    name: str
    version: str

    @abstractmethod
    def required_bars(self) -> int: ...

    @abstractmethod
    def evaluate(
        self, symbol: str, spec: InstrumentSpec, candles: list[Candle], now: datetime
    ) -> SignalEvaluation:
        """``candles`` must be CLOSED candles only, oldest first."""

    def leg_plan(
        self, signal: StrategySignal, quote: Quote, spec: InstrumentSpec
    ) -> tuple[LegPlan, float | None]:
        assert signal.side is not None and signal.stop_distance is not None
        if signal.side is Side.BUY:
            entry = quote.ask
            stop = spec.round_price_down(entry - signal.stop_distance)
            tp = spec.round_price(entry + signal.target_distance) if signal.target_distance else None
        else:
            entry = quote.bid
            stop = spec.round_price_up(entry + signal.stop_distance)
            tp = spec.round_price(entry - signal.target_distance) if signal.target_distance else None
        return LegPlan(signal.side, entry, stop), tp


def dual_leg_plans(quote: Quote, spec: InstrumentSpec, stop_pips: float) -> list[LegPlan]:
    """BUY at ask with stop below, SELL at bid with stop above - each from its own entry side."""
    d = spec.pips_to_price(stop_pips)
    return [
        LegPlan(Side.BUY, quote.ask, spec.round_price_down(quote.ask - d)),
        LegPlan(Side.SELL, quote.bid, spec.round_price_up(quote.bid + d)),
    ]
