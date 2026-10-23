"""Economic-calendar provider abstraction.

* ``SampleCalendarProvider`` - events loaded from a local JSON/YAML file. These
  are labelled ``origin=sample`` and are never presented as real news.
* ``MockCalendarProvider`` - in-memory events for automated tests.
* ``UnconfiguredCalendarProvider`` - the default for "real" news until a
  provider is chosen; it reports itself unavailable, which blocks
  news-triggered entries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from trading.news.models import EconomicEvent, EventOrigin, EventStatus, Impact


class CalendarUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarHealth:
    available: bool
    last_fetch: datetime | None
    detail: str


class CalendarProvider(Protocol):
    name: str
    origin: EventOrigin

    def events(self, start: datetime, end: datetime) -> list[EconomicEvent]: ...

    def health(self, now: datetime) -> CalendarHealth: ...


class MockCalendarProvider:
    name = "mock"
    origin = EventOrigin.MOCK

    def __init__(
        self,
        events: list[EconomicEvent] | None = None,
        available: bool = True,
        last_fetch: datetime | None = None,
    ) -> None:
        self._events = list(events or [])
        self.available = available
        self.last_fetch = last_fetch

    def events(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        if not self.available:
            raise CalendarUnavailable("mock provider marked unavailable")
        return [e for e in self._events if start <= e.scheduled_at < end]

    def health(self, now: datetime) -> CalendarHealth:
        return CalendarHealth(self.available, self.last_fetch or now, "mock")


class SampleCalendarProvider:
    """Events from a local JSON file: ``[{event_id, currency, name, scheduled_at, impact, ...}]``."""

    name = "sample-file"
    origin = EventOrigin.SAMPLE

    def __init__(self, path: Path | None = None, events: list[dict] | None = None) -> None:
        self.path = path
        raw = (
            events
            if events is not None
            else (json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else [])
        )
        self.loaded_at = datetime.now(UTC)
        self._events = [self._parse(e) for e in raw]

    def _parse(self, e: dict) -> EconomicEvent:
        ts = datetime.fromisoformat(e["scheduled_at"].replace("Z", "+00:00"))
        return EconomicEvent(
            event_id=str(e["event_id"]),
            currency=e["currency"].upper(),
            name=e["name"],
            scheduled_at=ts.astimezone(UTC),
            impact=Impact(e.get("impact", "high")),
            provider=self.name,
            origin=EventOrigin.SAMPLE,
            status=EventStatus(e.get("status", "scheduled")),
            forecast=e.get("forecast"),
            previous=e.get("previous"),
            actual=e.get("actual"),
            fetched_at=self.loaded_at,
        )

    def events(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        return [e for e in self._events if start <= e.scheduled_at < end]

    def health(self, now: datetime) -> CalendarHealth:
        return CalendarHealth(
            True,
            self.loaded_at,
            f"sample data from {self.path.name if self.path else 'memory'}; not real upcoming news",
        )


class UnconfiguredCalendarProvider:
    name = "unconfigured"
    origin = EventOrigin.PROVIDER

    def events(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        raise CalendarUnavailable("no economic-calendar provider is configured")

    def health(self, now: datetime) -> CalendarHealth:
        return CalendarHealth(False, None, "no economic-calendar provider is configured")
