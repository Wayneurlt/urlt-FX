from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.common import PageParams, paginate
from app.auth.deps import Principal, current_principal, require_admin
from app.db.base import get_db, session_scope, utcnow
from app.models import CircuitBreakerRow, IntegrationConfig, Job, Notification, SimBrokerState, TradingCycle
from app.services import dashboard_service, environment_service
from app.services.health_service import health_checks, worker_status
from app.services.system_settings import current_environment, get_setting, get_time_setting
from trading.interfaces.types import Environment
from trading.monitoring.health import HealthState

router = APIRouter(tags=["system"])


@router.get("/health")
def health(s: Session = Depends(get_db, scope="function")) -> dict:
    checks = health_checks(s)
    states = {c["state"] for c in checks}
    status = (
        "down"
        if HealthState.DOWN.value in states and checks[0]["state"] == "down"
        else ("degraded" if states & {"down", "degraded"} else "ok")
    )
    return {"status": status, "checks": checks}


def system_status(s: Session) -> dict[str, Any]:
    env = current_environment(s)
    alive, last = worker_status(s)
    if env is Environment.LIVE:
        mt5 = s.get(IntegrationConfig, "mt5")
        broker = {
            "name": "mt5",
            "connected": bool(mt5 and mt5.status == "connected"),
            "simulated": False,
            "detail": mt5.detail if mt5 else "not configured",
            "account_id": None,
            "margin_mode": None,
        }
    else:
        row = s.get(SimBrokerState, env.value)
        broker = {
            "name": "simulated",
            "connected": True,
            "simulated": True,
            "detail": "simulated execution (no real orders)",
            "account_id": f"SIM-{env.value.upper()}",
            "margin_mode": (row.settings or {}).get("margin_mode", "hedging") if row else "hedging",
        }
    mkt = get_time_setting(s, "last_market_update")
    cal = s.get(IntegrationConfig, "economic_calendar")
    cal_provider = (cal.settings or {}).get("provider", "none") if cal else "none"
    return {
        "environment": env.value,
        "live_trading_enabled": bool(get_setting(s, "live_trading_enabled")),
        "broker": broker,
        "market_data": {
            "provider": "synthetic" if env is not Environment.LIVE else "broker",
            "data_source": "synthetic" if env is not Environment.LIVE else "live",
            "last_update": mkt,
            "fresh": bool(mkt and (utcnow() - mkt).total_seconds() < 30),
        },
        "calendar": {
            "provider": cal_provider,
            "available": bool(cal and cal.enabled and cal.status == "connected"),
            "origin": "sample" if cal_provider == "sample" else None,
            "detail": cal.detail if cal else "",
        },
        "worker": {"alive": alive, "last_heartbeat": last},
        "last_reconciliation": get_time_setting(s, "last_reconciliation"),
        "active_breakers": s.scalar(
            select(func.count())
            .select_from(CircuitBreakerRow)
            .where(CircuitBreakerRow.active.is_(True), CircuitBreakerRow.environment == env.value)
        )
        or 0,
        "unread_notifications": s.scalar(
            select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
        )
        or 0,
        "server_time": utcnow(),
    }


@router.get("/system/status")
def status(_: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")) -> dict:
    return system_status(s)


@router.get("/system/live-readiness")
def readiness(_: Principal = Depends(require_admin), s: Session = Depends(get_db, scope="function")) -> dict:
    return environment_service.live_readiness(s)


class EnvIn(BaseModel):
    target: str
    confirm_text: str | None = None


@router.post("/system/environment")
def set_environment(
    body: EnvIn, p: Principal = Depends(require_admin), s: Session = Depends(get_db, scope="function")
) -> dict:
    return environment_service.switch_environment(s, body.target, p.name, body.confirm_text)


def _job_out(j: Job) -> dict:
    return {
        "id": j.id,
        "type": j.type,
        "status": j.status,
        "attempts": j.attempts,
        "max_attempts": j.max_attempts,
        "error": j.error,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "payload": j.payload,
        "result": j.result,
    }


@router.get("/system/jobs")
def jobs(
    status: str | None = None,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    q = select(Job).order_by(Job.created_at.desc())
    if status:
        q = q.where(Job.status == status)
    return paginate(s, q, p, _job_out)


@router.get("/system/metrics")
def metrics(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    from app.models import AuditLog

    env = current_environment(s)
    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "errors_today": s.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.severity.in_(("error", "critical")), AuditLog.created_at >= since)
        )
        or 0,
        "jobs": {st: n for st, n in s.execute(select(Job.status, func.count()).group_by(Job.status))},
        "cycles": {
            st: n
            for st, n in s.execute(
                select(TradingCycle.state, func.count())
                .where(TradingCycle.environment == env.value)
                .group_by(TradingCycle.state)
            )
        },
        "active_breakers": s.scalar(
            select(func.count()).select_from(CircuitBreakerRow).where(CircuitBreakerRow.active.is_(True))
        )
        or 0,
    }


@router.get("/dashboard/summary")
def dashboard_summary(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    return dashboard_service.summary(s, current_environment(s))


@router.get("/dashboard/charts")
def dashboard_charts(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    return dashboard_service.charts(s, current_environment(s), start, end)


@router.get("/stream")
async def stream(request: Request, _: Principal = Depends(current_principal)) -> StreamingResponse:
    """Server-sent events with status + newest notification. Clients fall back to polling."""

    async def gen():  # type: ignore[no-untyped-def]
        last_note = None
        while not await request.is_disconnected():
            with session_scope() as s:
                st = system_status(s)
                n = s.scalars(select(Notification).order_by(Notification.created_at.desc()).limit(1)).first()
                note = (
                    {"id": n.id, "title": n.title, "message": n.message, "severity": n.severity}
                    if n
                    else None
                )
            yield f"event: status\ndata: {json.dumps(st, default=str)}\n\n"
            if note and note["id"] != last_note:
                last_note = note["id"]
                yield f"event: notification\ndata: {json.dumps(note)}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
