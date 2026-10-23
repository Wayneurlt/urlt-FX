from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_id, utcnow


class RiskConfigVersion(Base):
    __tablename__ = "risk_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    config: Mapped[dict] = mapped_column()
    changes: Mapped[list] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")


class RiskState(Base):
    """Account-level risk coordination row (one per environment).

    ``version`` is bumped on every risk reservation (compare-and-swap) so two
    processes cannot both approve entries against the same budget.
    """

    __tablename__ = "risk_state"
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    day_start_at: Mapped[datetime | None] = mapped_column(default=None)
    day_start_equity: Mapped[float | None] = mapped_column(Float, default=None)
    peak_equity: Mapped[float | None] = mapped_column(Float, default=None)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class RiskReservation(Base):
    __tablename__ = "risk_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    environment: Mapped[str] = mapped_column(String(16))
    cycle_id: Mapped[str] = mapped_column(String(36), unique=True)
    bot_id: Mapped[str] = mapped_column(String(64))
    bot_kind: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(20))
    base_currency: Mapped[str] = mapped_column(String(3))
    quote_currency: Mapped[str] = mapped_column(String(3))
    correlation_group: Mapped[str | None] = mapped_column(String(40), default=None)
    planned_risk: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(default=None)


class RiskDecisionRow(Base):
    __tablename__ = "risk_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    environment: Mapped[str] = mapped_column(String(16))
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    signal_key: Mapped[str] = mapped_column(String(200))
    cycle_id: Mapped[str | None] = mapped_column(String(36), default=None)
    approved: Mapped[bool] = mapped_column(Boolean, index=True)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    planned_risk: Mapped[float] = mapped_column(Float, default=0.0)
    planned_risk_pct: Mapped[float] = mapped_column(Float, default=0.0)
    risk_limit_pct: Mapped[float] = mapped_column(Float, default=0.0)
    checks: Mapped[list] = mapped_column(default=list)
    reasons: Mapped[list] = mapped_column(default=list)
    legs: Mapped[list] = mapped_column(default=list)


class CircuitBreakerRow(Base):
    __tablename__ = "circuit_breakers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    environment: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(16))
    scope_key: Mapped[str | None] = mapped_column(String(64), default=None)
    reason: Mapped[str] = mapped_column(Text)
    critical: Mapped[bool] = mapped_column(Boolean)
    auto_clear: Mapped[bool] = mapped_column(Boolean)
    recovery: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    activated_at: Mapped[datetime] = mapped_column(default=utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(default=None)
    cleared_by: Mapped[str | None] = mapped_column(String(64), default=None)
    clear_note: Mapped[str | None] = mapped_column(Text, default=None)
    details: Mapped[dict] = mapped_column(default=dict)

    __table_args__ = (Index("ix_breakers_active_type", "active", "type", "scope", "scope_key"),)
