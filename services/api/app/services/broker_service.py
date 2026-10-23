"""Builds the execution adapter, market-data provider and calendar provider for an environment."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import APIError
from app.db.base import utcnow
from app.models import IntegrationConfig, SimBrokerState
from trading.adapters.base import BrokerAdapter
from trading.adapters.simulated_broker import SimulatedBroker, sim_config_from_dict
from trading.instruments.conversion import RateBook
from trading.instruments.spec import InstrumentRegistry
from trading.interfaces.types import DataSource, Environment
from trading.market_data.models import Candle, Quote
from trading.market_data.provider import MarketDataProvider, MarketStatus
from trading.news.provider import CalendarProvider, SampleCalendarProvider, UnconfiguredCalendarProvider
from trading.paper_trading.factory import build_market


class BrokerUnavailable(APIError):
    def __init__(self, message: str) -> None:
        super().__init__(409, "broker_unavailable", message)


def _persisting(name: str) -> Any:
    """Wrap a mutating SimulatedBroker method so state is committed after it runs (even if it raises)."""

    def method(self: PersistingSimBroker, *a: Any, **kw: Any) -> Any:
        try:
            return getattr(SimulatedBroker, name)(self, *a, **kw)
        finally:
            self._save()

    method.__name__ = name
    return method


class PersistingSimBroker(SimulatedBroker):
    """Simulated broker whose state is committed to the DB after every mutation."""

    def __init__(self, session: Session, environment: Environment, **kw: Any) -> None:
        self._session = session
        self._env = environment
        super().__init__(**kw)

    def _save(self) -> None:
        row = self._session.get(SimBrokerState, self._env.value)
        assert row is not None
        row.state = self.to_state()
        row.version += 1
        row.updated_at = utcnow()
        self._session.commit()

    submit_market_order = _persisting("submit_market_order")
    modify_stop = _persisting("modify_stop")
    close_position = _persisting("close_position")
    process_market = _persisting("process_market")
    connect = _persisting("connect")
    disconnect = _persisting("disconnect")
    reset = _persisting("reset")


DEFAULT_SIM_SETTINGS: dict[str, Any] = {
    "account_currency": "USD",
    "initial_balance": 10_000.0,
    "leverage": 30.0,
    "margin_mode": "hedging",
    "commission_per_lot_side": 3.5,
    "slippage_points_max": 3,
    "latency_ms": 50,
    "seed": 42,
}


def sim_state_row(s: Session, env: Environment) -> SimBrokerState:
    row = s.get(SimBrokerState, env.value)
    if row is None:
        row = SimBrokerState(
            environment=env.value,
            state=None,
            settings=dict(DEFAULT_SIM_SETTINGS),
            version=0,
            reset_at=utcnow(),
        )
        s.add(row)
        s.flush()
    return row


def market_provider(registry: InstrumentRegistry) -> MarketDataProvider:
    st = get_settings()
    return build_market(st.market_data_provider, st.synthetic_seed, registry)


class BrokerMarketData:
    """MarketDataProvider backed by a live broker connection."""

    def __init__(self, broker: BrokerAdapter) -> None:
        self.broker = broker
        self.source = DataSource.LIVE
        self.name = f"broker:{broker.name}"

    def get_quote(self, symbol: str, now: datetime) -> Quote:
        return self.broker.get_quote(symbol)

    def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        return self.broker.get_candles(symbol, timeframe, start, end)

    def market_status(self, symbol: str, now: datetime) -> MarketStatus:
        return self.broker.market_status(symbol)

    def rates(self, now: datetime) -> RateBook:
        rb = RateBook()
        for sym in ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"):
            try:
                rb.set(sym, self.broker.get_quote(sym).mid)
            except Exception:  # noqa: BLE001 - missing pairs simply aren't added; conversion then fails safely
                continue
        return rb


def build_broker(
    s: Session, env: Environment, registry: InstrumentRegistry, clock: Callable[[], datetime]
) -> tuple[BrokerAdapter, MarketDataProvider]:
    if env in (Environment.SIMULATION, Environment.PAPER):
        market = market_provider(registry)
        row = sim_state_row(s, env)
        broker = PersistingSimBroker(
            s,
            env,
            market=market,
            clock=clock,
            config=sim_config_from_dict(row.settings),
            registry=registry,
            state=row.state,
        )
        if row.state is None:
            broker._save()
        return broker, market
    # LIVE
    live = live_adapter(s)
    if live is None:
        raise BrokerUnavailable("live trading requires a configured and validated MT5 integration")
    return live, BrokerMarketData(live)


# One MT5 connection per process, reused across ticks. Re-initialising every tick means
# re-logging in, which MT5 treats as an account change (and may switch Algo Trading off).
_live_lock = threading.Lock()
_live_cache: dict[str, Any] = {}


def live_adapter(s: Session) -> BrokerAdapter | None:
    from app.services.integrations_service import build_mt5_adapter

    fresh = build_mt5_adapter(s)
    if fresh is None:
        return None
    st = fresh.settings
    key = hashlib.sha256(
        f"{st.login}|{st.server}|{st.terminal_path}|{st.symbol_suffix}|{st.password_secret}".encode()
    ).hexdigest()
    with _live_lock:
        cached = _live_cache.get("adapter")
        adapter = cached if cached is not None and _live_cache.get("key") == key else fresh
        _live_cache.update(adapter=adapter, key=key)
        if not adapter.check_connection().connected:
            adapter.connect()  # failure is reported by the caller's connection check
    return adapter


def calendar_provider(s: Session) -> CalendarProvider | None:
    row = s.get(IntegrationConfig, "economic_calendar")
    if row is None or not row.enabled:
        return None
    provider = (row.settings or {}).get("provider", "none")
    if provider == "sample":
        path = (row.settings or {}).get("path") or str(get_settings().sample_calendar_path)
        from pathlib import Path

        return SampleCalendarProvider(Path(path))
    return UnconfiguredCalendarProvider()
