from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_id, utcnow


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    bot_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    environment: Mapped[str | None] = mapped_column(String(16), default=None)
    data: Mapped[dict] = mapped_column(default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    severity: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    bot_id: Mapped[str | None] = mapped_column(String(64), default=None)
    cycle_id: Mapped[str | None] = mapped_column(String(36), default=None)
    read_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), default=None, unique=True)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    prefs: Mapped[dict] = mapped_column(default=dict)


class IntegrationConfig(Base):
    __tablename__ = "integration_configs"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict] = mapped_column(default=dict)  # non-secret settings only
    status: Mapped[str] = mapped_column(String(20), default="not_configured")
    detail: Mapped[str] = mapped_column(Text, default="")
    last_check: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class SecretRef(Base):
    """Encrypted credential. The encryption key lives only in the environment
    (FX_SECRET_ENCRYPTION_KEY) or an external secret manager - never in the DB."""

    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    integration_key: Mapped[str] = mapped_column(
        String(40), ForeignKey("integration_configs.key", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[str | None] = mapped_column(Text, default=None)
    external_ref: Mapped[str | None] = mapped_column(String(200), default=None)  # secret-manager identifier
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (Index("ux_secret_name", "integration_key", "name", unique=True),)


class SystemHealthEvent(Base):
    __tablename__ = "system_health_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    component: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text, default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), default=None)
    locked_at: Mapped[datetime | None] = mapped_column(default=None)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), default=None, unique=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    result: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)
    info: Mapped[dict] = mapped_column(default=dict)


class Lease(Base):
    """Cross-process mutual exclusion (e.g. one trading operation at a time per environment)."""

    __tablename__ = "leases"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(64), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(4))
    rows: Mapped[int] = mapped_column(Integer)
    start: Mapped[datetime] = mapped_column()
    end: Mapped[datetime] = mapped_column()
    source: Mapped[str] = mapped_column(String(40), default="historical")
    path: Mapped[str] = mapped_column(String(400))
    quality: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")


class InstrumentRow(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3))
    quote_currency: Mapped[str] = mapped_column(String(3))
    digits: Mapped[int] = mapped_column(Integer)
    tick_size: Mapped[float] = mapped_column(Float)
    pip_size: Mapped[float] = mapped_column(Float)
    contract_size: Mapped[float] = mapped_column(Float)
    min_volume: Mapped[float] = mapped_column(Float)
    max_volume: Mapped[float] = mapped_column(Float)
    volume_step: Mapped[float] = mapped_column(Float)
    min_stop_distance: Mapped[float] = mapped_column(Float, default=0.0)
    freeze_level: Mapped[float] = mapped_column(Float, default=0.0)
    tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    broker_symbol: Mapped[str | None] = mapped_column(String(30), default=None)
    correlation_group: Mapped[str | None] = mapped_column(String(40), default=None)
    source: Mapped[str] = mapped_column(String(16), default="default")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    bot_kind: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(20))
    start: Mapped[datetime] = mapped_column()
    end: Mapped[datetime] = mapped_column()
    data_source: Mapped[str] = mapped_column(String(20))
    dataset_id: Mapped[str | None] = mapped_column(String(36), default=None)
    config: Mapped[dict] = mapped_column()
    metrics: Mapped[dict] = mapped_column(default=dict)
    warnings: Mapped[list] = mapped_column(default=list)
    assumptions: Mapped[list] = mapped_column(default=list)
    counters: Mapped[dict] = mapped_column(default=dict)
    equity_curve: Mapped[list] = mapped_column(default=list)
    daily_equity: Mapped[list] = mapped_column(default=list)
    data_fingerprint: Mapped[str | None] = mapped_column(String(32), default=None)
    reproducibility: Mapped[dict] = mapped_column(default=dict)
    validation: Mapped[dict] = mapped_column(default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class BacktestMetric(Base):
    __tablename__ = "backtest_metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float, default=None)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))  # trade | cycle
    cycle_ref: Mapped[str] = mapped_column(String(40))
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    data: Mapped[dict] = mapped_column()

    __table_args__ = (Index("ix_bt_trades_run_kind_seq", "run_id", "kind", "seq"),)


__all__ = [
    "AuditLog",
    "BacktestMetric",
    "BacktestRun",
    "BacktestTrade",
    "Dataset",
    "InstrumentRow",
    "IntegrationConfig",
    "Job",
    "Lease",
    "Notification",
    "NotificationPreference",
    "SecretRef",
    "SystemHealthEvent",
    "SystemSetting",
    "WorkerHeartbeat",
]
