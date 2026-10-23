from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class HealthCheck:
    name: str
    state: HealthState
    detail: str
    checked_at: datetime
    last_success: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "detail": self.detail,
            "checked_at": self.checked_at.isoformat(),
            "last_success": self.last_success.isoformat() if self.last_success else None,
        }


def overall(checks: list[HealthCheck]) -> HealthState:
    states = {c.state for c in checks}
    if HealthState.DOWN in states:
        return HealthState.DOWN
    if HealthState.DEGRADED in states or HealthState.UNKNOWN in states:
        return HealthState.DEGRADED
    return HealthState.OK
