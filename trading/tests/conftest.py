from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from trading.adapters.simulated_broker import SimConfig, SimulatedBroker
from trading.execution.records import CycleRecord, InMemoryStore
from trading.instruments.spec import InstrumentRegistry
from trading.market_data.scripted import ScriptedMarketData
from trading.risk.config import GlobalRiskConfig
from trading.strategies.news_volatility.config import NewsVolatilityConfig


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t = self.t + timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2025, 3, 5, 13, 0, tzinfo=UTC))  # a Wednesday


@pytest.fixture
def registry() -> InstrumentRegistry:
    return InstrumentRegistry.with_defaults()


@pytest.fixture
def market(clock):
    m = ScriptedMarketData(clock)
    m.set("EURUSD", 1.10000, 1.10010)
    m.set("USDJPY", 150.000, 150.010)
    m.set("GBPUSD", 1.27000, 1.27012)
    return m


@pytest.fixture
def broker(market, clock, registry):
    cfg = SimConfig(slippage_points_max=0, latency_ms=0, commission_per_lot_side=3.5, seed=1)
    return SimulatedBroker(market, clock, cfg, registry)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def nv_config() -> NewsVolatilityConfig:
    return NewsVolatilityConfig()


@pytest.fixture
def global_risk() -> GlobalRiskConfig:
    return GlobalRiskConfig()


def new_cycle(
    clock, symbol="EURUSD", bot_id="bot-nv", magic=1001, kind="dual_entry", state="validating"
) -> CycleRecord:
    return CycleRecord(
        id=str(uuid.uuid4()),
        kind=kind,
        bot_id=bot_id,
        magic=magic,
        strategy_version="1.0.0",
        trigger_type="volatility",
        symbol=symbol,
        environment="simulation",
        state=state,
        created_at=clock(),
        updated_at=clock(),
        signal_key=f"k-{uuid.uuid4()}",
    )


@pytest.fixture(autouse=True)
def no_real_mt5(monkeypatch):
    """Tests must never reach a real MetaTrader 5 terminal installed on this machine."""
    from trading.adapters.mt5 import adapter as mt5_adapter

    def blocked():
        raise mt5_adapter.MT5Unavailable("real MetaTrader5 is blocked in tests")

    monkeypatch.setattr(mt5_adapter, "load_mt5_module", blocked)
