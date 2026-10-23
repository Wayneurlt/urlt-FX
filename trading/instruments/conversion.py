"""Account-currency conversion and pip/tick value calculation.

Conversion never guesses: if a required rate is missing the caller gets
``ConversionUnavailable`` and must block automatic sizing.
"""

from __future__ import annotations

from collections.abc import Mapping

from trading.instruments.spec import InstrumentSpec


class ConversionUnavailable(LookupError):
    pass


class RateBook:
    """Mid rates keyed by 6-letter pair (e.g. ``EURUSD`` -> 1.0850)."""

    def __init__(self, rates: Mapping[str, float] | None = None) -> None:
        self._rates: dict[str, float] = {}
        for k, v in (rates or {}).items():
            self.set(k, v)

    def set(self, pair: str, rate: float) -> None:
        if len(pair) < 6 or rate <= 0:
            raise ValueError(f"invalid rate {pair}={rate}")
        self._rates[pair[:6].upper()] = float(rate)

    def _direct(self, f: str, t: str) -> float | None:
        if f == t:
            return 1.0
        if self._rates.get(f + t):
            return self._rates[f + t]
        inv = self._rates.get(t + f)
        return 1.0 / inv if inv else None

    def rate(self, from_ccy: str, to_ccy: str) -> float:
        """How many ``to_ccy`` one unit of ``from_ccy`` buys (direct, inverse, or one cross via USD/EUR)."""
        f, t = from_ccy.upper(), to_ccy.upper()
        r = self._direct(f, t)
        if r is not None:
            return r
        for via in ("USD", "EUR"):
            a, b = self._direct(f, via), self._direct(via, t)
            if a is not None and b is not None:
                return a * b
        raise ConversionUnavailable(f"no rate available to convert {f} -> {t}")

    def as_dict(self) -> dict[str, float]:
        return dict(self._rates)


def tick_value_account(spec: InstrumentSpec, account_ccy: str, rates: RateBook) -> float:
    """Money value (account currency) of a one-tick move for 1.0 lot."""
    return spec.tick_value_in_quote() * rates.rate(spec.quote_currency, account_ccy)


def pip_value_account(spec: InstrumentSpec, account_ccy: str, rates: RateBook) -> float:
    return spec.contract_size * spec.pip_size * rates.rate(spec.quote_currency, account_ccy)


def price_distance_value(
    spec: InstrumentSpec, distance: float, volume: float, account_ccy: str, rates: RateBook
) -> float:
    """Money value of a ``distance`` price move for ``volume`` lots in account currency."""
    return distance * spec.contract_size * volume * rates.rate(spec.quote_currency, account_ccy)
