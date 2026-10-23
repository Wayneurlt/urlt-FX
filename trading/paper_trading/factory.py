"""Builds the simulated execution stack used for simulation and paper trading.

Paper trading = simulated execution (``SimulatedBroker``) against the
configured market-data provider. Until a real data provider is configured,
that provider is the SYNTHETIC generator and every price is labelled so.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from trading.adapters.simulated_broker import SimulatedBroker, sim_config_from_dict
from trading.instruments.spec import InstrumentRegistry
from trading.market_data.provider import MarketDataProvider
from trading.market_data.synthetic import SyntheticMarketData


def build_market(provider: str, seed: int, registry: InstrumentRegistry) -> MarketDataProvider:
    if provider == "synthetic":
        return SyntheticMarketData(seed=seed, registry=registry)
    raise ValueError(f"market-data provider {provider!r} is not configured")


def build_simulated_broker(
    market: MarketDataProvider,
    clock: Callable[[], datetime],
    registry: InstrumentRegistry,
    sim_settings: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> SimulatedBroker:
    return SimulatedBroker(market, clock, sim_config_from_dict(sim_settings), registry, state)
