from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Impact(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventStatus(StrEnum):
    SCHEDULED = "scheduled"
    RELEASED = "released"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"


class EventOrigin(StrEnum):
    """Whether an event is real upcoming news or sample/test data."""

    PROVIDER = "provider"  # a configured real calendar provider
    SAMPLE = "sample"  # manually seeded sample data - never real upcoming news
    MOCK = "mock"  # automated tests


@dataclass(frozen=True)
class EconomicEvent:
    event_id: str
    currency: str
    name: str
    scheduled_at: datetime  # UTC
    impact: Impact
    provider: str
    origin: EventOrigin
    status: EventStatus = EventStatus.SCHEDULED
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.scheduled_at.tzinfo is None:
            raise ValueError("scheduled_at must be timezone-aware UTC")
