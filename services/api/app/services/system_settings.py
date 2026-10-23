from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import SystemSetting
from trading.interfaces.types import Environment

DEFAULTS: dict[str, Any] = {
    "environment": Environment.SIMULATION.value,
    "live_trading_enabled": False,
    "last_market_update": None,
    "last_reconciliation": None,
    "last_calendar_sync": None,
}


def get_setting(s: Session, key: str) -> Any:
    row = s.get(SystemSetting, key)
    if row is None:
        return DEFAULTS.get(key)
    return row.value.get("v")


def set_setting(s: Session, key: str, value: Any, actor: str = "system") -> None:
    row = s.get(SystemSetting, key)
    if row is None:
        s.add(SystemSetting(key=key, value={"v": value}, updated_by=actor, updated_at=utcnow()))
    else:
        row.value = {"v": value}
        row.updated_by = actor
        row.updated_at = utcnow()


def current_environment(s: Session) -> Environment:
    return Environment(get_setting(s, "environment") or Environment.SIMULATION.value)


def get_time_setting(s: Session, key: str) -> datetime | None:
    v = get_setting(s, key)
    return datetime.fromisoformat(v) if v else None
