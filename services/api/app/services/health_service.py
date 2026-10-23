from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import IntegrationConfig, Job, WorkerHeartbeat
from app.services.system_settings import current_environment, get_time_setting
from trading.monitoring.health import HealthCheck, HealthState

WORKER_STALE = timedelta(seconds=20)


def worker_status(s: Session) -> tuple[bool, Any]:
    hb = s.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen.desc()).limit(1)).first()
    if hb is None:
        return False, None
    # A slow (but running) loop is reported separately; only a missing heartbeat means "down".
    tick = float((hb.info or {}).get("tick_seconds") or 0.0)
    stale_after = max(WORKER_STALE, timedelta(seconds=2 * tick + 10))
    return utcnow() - hb.last_seen < stale_after, hb.last_seen


def worker_tick_seconds(s: Session) -> float | None:
    hb = s.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen.desc()).limit(1)).first()
    v = (hb.info or {}).get("tick_seconds") if hb else None
    return float(v) if v is not None else None


def health_checks(s: Session) -> list[dict[str, Any]]:
    now = utcnow()
    out: list[HealthCheck] = []
    try:
        s.execute(text("SELECT 1"))
        out.append(HealthCheck("database", HealthState.OK, "reachable", now, now))
    except Exception as exc:  # noqa: BLE001
        out.append(HealthCheck("database", HealthState.DOWN, f"{type(exc).__name__}", now))
        return [c.as_dict() for c in out]
    alive, last = worker_status(s)
    out.append(
        HealthCheck(
            "worker",
            HealthState.OK if alive else HealthState.DOWN,
            "heartbeat fresh"
            if alive
            else ("no heartbeat yet - start the worker" if last is None else "heartbeat stale"),
            now,
            last,
        )
    )
    tick = worker_tick_seconds(s)
    target = get_settings().worker_tick_seconds
    if alive and tick is not None:
        slow = tick > max(5.0, 3 * target)
        out.append(
            HealthCheck(
                "trading_loop",
                HealthState.DEGRADED if slow else HealthState.OK,
                f"{tick:.1f}s per tick (target {target:g}s)"
                + (
                    " - stops and trailing react this slowly; the database is probably too far from the app (see docs/DEPLOYMENT.md)"
                    if slow
                    else ""
                ),
                now,
                last,
            )
        )
    rec = get_time_setting(s, "last_reconciliation")
    rec_window = timedelta(seconds=max(30.0, 3 * (tick or 0.0)))
    out.append(
        HealthCheck(
            "reconciliation",
            HealthState.OK if rec and now - rec < rec_window else HealthState.DEGRADED,
            "recent" if rec and now - rec < rec_window else "no recent broker reconciliation",
            now,
            rec,
        )
    )
    mkt = get_time_setting(s, "last_market_update")
    env = current_environment(s)
    out.append(
        HealthCheck(
            "market_data",
            HealthState.OK if mkt and now - mkt < timedelta(seconds=30) else HealthState.UNKNOWN,
            f"{'synthetic' if env.value != 'live' else 'broker'} quotes "
            + ("fresh" if mkt and now - mkt < timedelta(seconds=30) else "not recently updated"),
            now,
            mkt,
        )
    )
    cal = s.get(IntegrationConfig, "economic_calendar")
    if cal is None or not cal.enabled:
        out.append(
            HealthCheck(
                "economic_calendar",
                HealthState.NOT_CONFIGURED,
                "no calendar provider; news entries blocked",
                now,
            )
        )
    else:
        out.append(
            HealthCheck(
                "economic_calendar",
                HealthState.OK if cal.status == "connected" else HealthState.DEGRADED,
                cal.detail or cal.status,
                now,
                cal.last_check,
            )
        )
    failed = (
        s.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_(("failed", "dead")), Job.created_at >= now - timedelta(days=1))
        )
        or 0
    )
    out.append(
        HealthCheck(
            "jobs",
            HealthState.OK if not failed else HealthState.DEGRADED,
            f"{failed} failed job(s) in 24h",
            now,
        )
    )
    return [c.as_dict() for c in out]
