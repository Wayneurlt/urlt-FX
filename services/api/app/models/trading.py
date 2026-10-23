from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, new_id, utcnow


class TradingSignal(Base):
    """Every candidate signal, unique by dedupe key (prevents duplicate cycles)."""

    __tablename__ = "trading_signals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    bot_id: Mapped[str] = mapped_column(String(64), ForeignKey("bots.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    symbol: Mapped[str] = mapped_column(String(20))
    trigger_type: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str | None] = mapped_column(String(100), default=None)
    candle_time: Mapped[datetime | None] = mapped_column(default=None)
    details: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|accepted|rejected
    cycle_id: Mapped[str | None] = mapped_column(String(36), default=None)
    environment: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class TradingCycle(Base):
    __tablename__ = "trading_cycles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # dual_entry | single
    bot_id: Mapped[str] = mapped_column(String(64), ForeignKey("bots.id"), index=True)
    magic: Mapped[int] = mapped_column(Integer)
    strategy_version: Mapped[str] = mapped_column(String(16))
    trigger_type: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str | None] = mapped_column(String(100), default=None)
    signal_key: Mapped[str] = mapped_column(String(200), unique=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    environment: Mapped[str] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(String(24), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    planned_risk: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str | None] = mapped_column(String(40), default=None)
    risk_decision: Mapped[dict] = mapped_column(default=dict)
    signal: Mapped[dict] = mapped_column(default=dict)
    config_snapshot: Mapped[dict] = mapped_column(default=dict)
    trailing: Mapped[dict | None] = mapped_column(default=None)
    management: Mapped[dict] = mapped_column(default=dict)
    notes: Mapped[list] = mapped_column(default=list)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    trailing_activated_at: Mapped[datetime | None] = mapped_column(default=None)

    legs: Mapped[list[PositionLeg]] = relationship(
        back_populates="cycle", cascade="all, delete-orphan", order_by="PositionLeg.side"
    )


class OrderIntent(Base):
    """Durable order intent, written BEFORE the order is sent to the broker."""

    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # leg id
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trading_cycles.id", ondelete="CASCADE"), index=True
    )
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))
    requested_volume: Mapped[float] = mapped_column(Float)
    filled_volume: Mapped[float] = mapped_column(Float, default=0.0)
    intent_status: Mapped[str] = mapped_column(String(20), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    fill_price: Mapped[float | None] = mapped_column(Float, default=None)
    reject_reason: Mapped[str | None] = mapped_column(Text, default=None)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class PositionLeg(Base):
    """One leg (position) of a cycle, with its full lifecycle."""

    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trading_cycles.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(4))
    client_order_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    intent_status: Mapped[str] = mapped_column(String(20))
    requested_volume: Mapped[float] = mapped_column(Float)
    filled_volume: Mapped[float] = mapped_column(Float, default=0.0)
    position_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    expected_price: Mapped[float | None] = mapped_column(Float, default=None)
    fill_price: Mapped[float | None] = mapped_column(Float, default=None)
    fill_time: Mapped[datetime | None] = mapped_column(default=None)
    intended_sl: Mapped[float | None] = mapped_column(Float, default=None)
    confirmed_sl: Mapped[float | None] = mapped_column(Float, default=None)
    sl_verified_at: Mapped[datetime | None] = mapped_column(default=None)
    intended_tp: Mapped[float | None] = mapped_column(Float, default=None)
    tp: Mapped[float | None] = mapped_column(Float, default=None)
    exit_price: Mapped[float | None] = mapped_column(Float, default=None)
    exit_time: Mapped[datetime | None] = mapped_column(default=None, index=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    swap: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    floating_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_pips: Mapped[float | None] = mapped_column(Float, default=None)
    reject_reason: Mapped[str | None] = mapped_column(Text, default=None)
    stop_history: Mapped[list] = mapped_column(default=list)

    cycle: Mapped[TradingCycle] = relationship(back_populates="legs")


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trading_cycles.id", ondelete="CASCADE"), index=True
    )
    leg_id: Mapped[str] = mapped_column(String(80), index=True)
    entry: Mapped[str] = mapped_column(String(4))  # in | out
    side: Mapped[str] = mapped_column(String(4))
    volume: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    time: Mapped[datetime] = mapped_column()
    reason: Mapped[str] = mapped_column(String(32), default="")
    profit: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    swap: Mapped[float] = mapped_column(Float, default=0.0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("leg_id", "entry"),)


class StopModification(Base):
    __tablename__ = "stop_modifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trading_cycles.id", ondelete="CASCADE"), index=True
    )
    leg_id: Mapped[str] = mapped_column(String(80), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    time: Mapped[datetime] = mapped_column()
    action: Mapped[str] = mapped_column(String(32))
    sl: Mapped[float | None] = mapped_column(Float, default=None)
    previous: Mapped[float | None] = mapped_column(Float, default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (UniqueConstraint("leg_id", "seq"),)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    time: Mapped[datetime] = mapped_column(default=utcnow)
    environment: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float, default=0.0)
    free_margin: Mapped[float] = mapped_column(Float, default=0.0)
    margin_level: Mapped[float | None] = mapped_column(Float, default=None)
    floating_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="simulated")

    __table_args__ = (Index("ix_account_snapshots_env_time", "environment", "time"),)


class SimBrokerState(Base):
    """Persisted state of the simulated broker per environment (simulation / paper)."""

    __tablename__ = "sim_broker_state"
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[dict | None] = mapped_column(default=None)
    settings: Mapped[dict] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    reset_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class EconomicEventRow(Base):
    __tablename__ = "economic_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3), index=True)
    name: Mapped[str] = mapped_column(String(200))
    scheduled_at: Mapped[datetime] = mapped_column(index=True)
    impact: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    forecast: Mapped[str | None] = mapped_column(String(40), default=None)
    previous: Mapped[str | None] = mapped_column(String(40), default=None)
    actual: Mapped[str | None] = mapped_column(String(40), default=None)
    fetched_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (UniqueConstraint("provider", "event_id"),)


class Notes(Base):
    """Free-form operator notes attached to cycles (kept separate from audit logs)."""

    __tablename__ = "cycle_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trading_cycles.id", ondelete="CASCADE"), index=True
    )
    author: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
