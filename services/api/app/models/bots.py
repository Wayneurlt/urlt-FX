from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_id, utcnow


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (UniqueConstraint("name", "version"),)


class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    magic: Mapped[int] = mapped_column(Integer, unique=True)  # broker-side identifier for ownership
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="disabled")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_evaluation_at: Mapped[datetime | None] = mapped_column(default=None)
    last_execution_at: Mapped[datetime | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    last_evaluation: Mapped[dict] = mapped_column(default=dict)  # latest diagnostics snapshot
    runtime: Mapped[dict] = mapped_column(default=dict)  # e.g. last volatility triggers
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class BotConfig(Base):
    __tablename__ = "bot_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    bot_id: Mapped[str] = mapped_column(String(64), ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(String(300), default="")

    __table_args__ = (UniqueConstraint("bot_id", "version"),)
