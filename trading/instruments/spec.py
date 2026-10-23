"""Instrument specifications and price/volume arithmetic.

Prices and volumes are floats in this codebase; every value that is sent to a
broker or persisted is first snapped to the instrument's tick size / volume
step with Decimal arithmetic so floating-point noise never leaks into orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal


class InvalidInstrumentSpec(ValueError):
    pass


class UnknownInstrument(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: str  # canonical symbol, e.g. EURUSD
    base_currency: str
    quote_currency: str
    digits: int
    tick_size: float
    pip_size: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    min_stop_distance: float = 0.0  # in price units (broker "stops level")
    freeze_level: float = 0.0  # in price units
    tradable: bool = True
    broker_symbol: str | None = None  # e.g. EURUSD.r, EURUSDm
    source: str = "default"  # "default" | "broker" | "user"
    correlation_group: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.validate()

    @property
    def name_at_broker(self) -> str:
        return self.broker_symbol or self.symbol

    def validate(self) -> None:
        errors: list[str] = []
        if len(self.base_currency) != 3 or len(self.quote_currency) != 3:
            errors.append("currencies must be 3-letter ISO codes")
        for name in ("tick_size", "pip_size", "contract_size", "min_volume", "max_volume", "volume_step"):
            v = getattr(self, name)
            if not (isinstance(v, int | float) and math.isfinite(v) and v > 0):
                errors.append(f"{name} must be a positive finite number")
        if self.digits < 0 or self.digits > 10:
            errors.append("digits out of range")
        if not errors:
            if self.max_volume < self.min_volume:
                errors.append("max_volume < min_volume")
            if self.pip_size < self.tick_size:
                errors.append("pip_size smaller than tick_size")
            if abs(self.tick_size - 10 ** (-self.digits)) > 1e-12 and self.tick_size < 10 ** (-self.digits):
                errors.append("tick_size finer than price digits")
            if self.min_stop_distance < 0 or self.freeze_level < 0:
                errors.append("stop/freeze levels must be >= 0")
        if errors:
            raise InvalidInstrumentSpec(f"{self.symbol}: " + "; ".join(errors))

    # --- conversions -------------------------------------------------------
    def pips_to_price(self, pips: float) -> float:
        return self.round_price(pips * self.pip_size)

    def price_to_pips(self, distance: float) -> float:
        return distance / self.pip_size

    def round_price(self, price: float, mode: str = ROUND_HALF_EVEN) -> float:
        tick = Decimal(str(self.tick_size))
        q = (Decimal(str(price)) / tick).quantize(Decimal(1), rounding=mode) * tick
        return float(round(q, self.digits))

    def round_price_down(self, price: float) -> float:
        return self.round_price(price, ROUND_FLOOR)

    def round_price_up(self, price: float) -> float:
        return self.round_price(price, ROUND_CEILING)

    def normalize_volume(self, volume: float) -> float:
        """Round DOWN to the volume step. Returns 0.0 if below min volume.

        Never rounds up: rounding up would silently increase risk beyond the
        approved amount.
        """
        if volume <= 0 or not math.isfinite(volume):
            return 0.0
        step = Decimal(str(self.volume_step))
        v = (Decimal(str(volume)) / step).quantize(Decimal(1), rounding=ROUND_DOWN) * step
        fv = float(v)
        if fv < self.min_volume - 1e-12:
            return 0.0
        return min(fv, self.max_volume)

    def is_valid_volume(self, volume: float) -> bool:
        if volume < self.min_volume - 1e-12 or volume > self.max_volume + 1e-12:
            return False
        steps = Decimal(str(volume)) / Decimal(str(self.volume_step))
        return steps == steps.to_integral_value()

    def tick_value_in_quote(self) -> float:
        """Value of one tick move for 1.0 lot, in the quote currency."""
        return self.contract_size * self.tick_size

    def same_price(self, a: float, b: float) -> bool:
        return abs(a - b) < self.tick_size / 2


# Generic defaults for common majors / crosses. These are typical retail values
# and MUST be overridden by broker-reported specs once a broker is connected.
_DEFAULT_SPECS: list[InstrumentSpec] = [
    InstrumentSpec(
        "EURUSD",
        "EUR",
        "USD",
        5,
        0.00001,
        0.0001,
        100_000,
        0.01,
        100,
        0.01,
        0.0,
        0.0,
        correlation_group="USD_MAJORS",
    ),
    InstrumentSpec(
        "GBPUSD",
        "GBP",
        "USD",
        5,
        0.00001,
        0.0001,
        100_000,
        0.01,
        100,
        0.01,
        0.0,
        0.0,
        correlation_group="USD_MAJORS",
    ),
    InstrumentSpec(
        "AUDUSD",
        "AUD",
        "USD",
        5,
        0.00001,
        0.0001,
        100_000,
        0.01,
        100,
        0.01,
        0.0,
        0.0,
        correlation_group="COMMODITY",
    ),
    InstrumentSpec(
        "NZDUSD",
        "NZD",
        "USD",
        5,
        0.00001,
        0.0001,
        100_000,
        0.01,
        100,
        0.01,
        0.0,
        0.0,
        correlation_group="COMMODITY",
    ),
    InstrumentSpec(
        "USDJPY", "USD", "JPY", 3, 0.001, 0.01, 100_000, 0.01, 100, 0.01, 0.0, 0.0, correlation_group="JPY"
    ),
    InstrumentSpec(
        "USDCHF",
        "USD",
        "CHF",
        5,
        0.00001,
        0.0001,
        100_000,
        0.01,
        100,
        0.01,
        0.0,
        0.0,
        correlation_group="USD_MAJORS",
    ),
    InstrumentSpec(
        "USDCAD",
        "USD",
        "CAD",
        5,
        0.00001,
        0.0001,
        100_000,
        0.01,
        100,
        0.01,
        0.0,
        0.0,
        correlation_group="COMMODITY",
    ),
    InstrumentSpec(
        "EURJPY", "EUR", "JPY", 3, 0.001, 0.01, 100_000, 0.01, 100, 0.01, 0.0, 0.0, correlation_group="JPY"
    ),
    InstrumentSpec(
        "GBPJPY", "GBP", "JPY", 3, 0.001, 0.01, 100_000, 0.01, 100, 0.01, 0.0, 0.0, correlation_group="JPY"
    ),
    InstrumentSpec(
        "EURGBP",
        "EUR",
        "GBP",
        5,
        0.00001,
        0.0001,
        100_000,
        0.01,
        100,
        0.01,
        0.0,
        0.0,
        correlation_group="EUR_CROSSES",
    ),
]


class InstrumentRegistry:
    """Holds validated specs. Broker-provided specs override defaults."""

    def __init__(self, specs: list[InstrumentSpec] | None = None) -> None:
        self._specs: dict[str, InstrumentSpec] = {}
        self._broker_alias: dict[str, str] = {}
        for s in specs if specs is not None else _DEFAULT_SPECS:
            self.add(s)

    @classmethod
    def with_defaults(cls) -> InstrumentRegistry:
        return cls(list(_DEFAULT_SPECS))

    def add(self, spec: InstrumentSpec) -> None:
        spec.validate()
        self._specs[spec.symbol] = spec
        if spec.broker_symbol:
            self._broker_alias[spec.broker_symbol] = spec.symbol

    def apply_broker_spec(self, broker_spec: InstrumentSpec) -> InstrumentSpec:
        """Override a default spec with validated broker-reported values."""
        merged = replace(broker_spec, source="broker")
        merged.validate()
        self.add(merged)
        return merged

    def get(self, symbol: str) -> InstrumentSpec:
        sym = self._broker_alias.get(symbol, symbol)
        try:
            return self._specs[sym]
        except KeyError as exc:
            raise UnknownInstrument(symbol) from exc

    def has(self, symbol: str) -> bool:
        return symbol in self._specs or symbol in self._broker_alias

    def symbols(self) -> list[str]:
        return sorted(self._specs)

    def all(self) -> list[InstrumentSpec]:
        return [self._specs[s] for s in self.symbols()]


def default_specs() -> list[InstrumentSpec]:
    return list(_DEFAULT_SPECS)
