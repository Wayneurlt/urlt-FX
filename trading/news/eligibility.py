from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading.instruments.spec import InstrumentSpec
from trading.news.models import EconomicEvent, EventStatus
from trading.news.provider import CalendarHealth
from trading.strategies.news_volatility.config import NewsWindowConfig


@dataclass(frozen=True)
class NewsCandidate:
    event: EconomicEvent
    symbol: str
    window_start: datetime
    window_end: datetime
    dedupe_key: str


def window_for(event: EconomicEvent, cfg: NewsWindowConfig) -> tuple[datetime, datetime]:
    return (
        event.scheduled_at - timedelta(seconds=cfg.pre_event_seconds),
        event.scheduled_at + timedelta(seconds=cfg.post_event_seconds),
    )


def in_window(event: EconomicEvent, now: datetime, cfg: NewsWindowConfig) -> bool:
    start, end = window_for(event, cfg)
    return start <= now <= end


def feed_is_fresh(health: CalendarHealth, now: datetime, cfg: NewsWindowConfig) -> tuple[bool, str]:
    if not health.available:
        return False, f"calendar unavailable: {health.detail}"
    if health.last_fetch is None:
        return False, "calendar has never been fetched"
    age_min = (now - health.last_fetch).total_seconds() / 60
    if age_min > cfg.max_feed_age_minutes:
        return False, f"calendar data is stale ({age_min:.0f} min old > {cfg.max_feed_age_minutes})"
    return True, "fresh"


def news_dedupe_key(bot_id: str, event: EconomicEvent, symbol: str, cfg: NewsWindowConfig) -> str:
    return f"news:{bot_id}:{event.event_id}:{symbol}:{cfg.pre_event_seconds}:{cfg.post_event_seconds}"


def eligible_candidates(
    bot_id: str,
    events: list[EconomicEvent],
    instruments: list[InstrumentSpec],
    now: datetime,
    cfg: NewsWindowConfig,
    processed_keys: set[str],
) -> list[NewsCandidate]:
    out: list[NewsCandidate] = []
    for ev in events:
        if ev.status in (EventStatus.CANCELLED, EventStatus.POSTPONED):
            continue
        if ev.currency not in cfg.currencies or ev.impact.value not in cfg.impact_levels:
            continue
        if not in_window(ev, now, cfg):
            continue
        start, end = window_for(ev, cfg)
        for spec in instruments:
            if ev.currency not in (spec.base_currency, spec.quote_currency):
                continue
            key = news_dedupe_key(bot_id, ev, spec.symbol, cfg)
            if key in processed_keys:
                continue
            out.append(NewsCandidate(ev, spec.symbol, start, end, key))
    return out
