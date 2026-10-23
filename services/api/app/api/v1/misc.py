"""Market & news, integrations & AI, notifications & logs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.api.v1.common import PageParams, event_out, log_out, notification_out, paginate
from app.auth.deps import Principal, current_principal, require_admin
from app.core.errors import bad_request, not_found
from app.db.base import get_db, utcnow
from app.models import (
    AuditLog,
    EconomicEventRow,
    InstrumentRow,
    IntegrationConfig,
    Notification,
    NotificationPreference,
)
from app.services import ai_service, integrations_service
from app.services.audit import NOTIFY_KINDS
from app.services.broker_service import market_provider
from app.services.instruments import load_registry
from app.services.system_settings import current_environment, get_time_setting
from trading.market_data.models import TIMEFRAME_MINUTES

router = APIRouter()


# --- market & news -----------------------------------------------------------------
@router.get("/market/instruments", tags=["market"])
def instruments(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    return [
        {c.name: getattr(r, c.name) for c in InstrumentRow.__table__.columns}
        for r in s.scalars(select(InstrumentRow).order_by(InstrumentRow.symbol))
    ]


@router.get("/market/quotes", tags=["market"])
def quotes(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    if current_environment(s).value == "live":
        return []  # live quotes come from the broker via the worker; not polled from the API process
    reg, _r = load_registry(s)
    mkt = market_provider(reg)
    now = utcnow()
    out = []
    for spec in reg.all():
        try:
            q = mkt.get_quote(spec.symbol, now)
        except Exception:  # noqa: BLE001
            continue
        out.append(
            {
                "symbol": spec.symbol,
                "bid": q.bid,
                "ask": q.ask,
                "spread_pips": round(spec.price_to_pips(q.spread), 2),
                "time": q.time,
                "data_source": q.source.value,
                "fresh": q.is_fresh(now, 5),
            }
        )
    return out


@router.get("/market/candles", tags=["market"])
def candles(
    symbol: str,
    timeframe: str = "M15",
    start: datetime | None = None,
    end: datetime | None = None,
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    if timeframe not in TIMEFRAME_MINUTES:
        raise bad_request("unsupported timeframe")
    reg, _r = load_registry(s)
    if not reg.has(symbol):
        raise not_found("instrument")
    end = end or utcnow()
    start = start or end - timedelta(minutes=TIMEFRAME_MINUTES[timeframe] * 200)
    if (end - start) / timedelta(minutes=TIMEFRAME_MINUTES[timeframe]) > 5000:
        raise bad_request("too many candles requested (max 5000)")
    mkt = market_provider(reg)
    cs = mkt.get_candles(symbol, timeframe, start, end)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "data_source": mkt.source.value,
        "candles": [
            {
                "time": c.time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "spread": c.spread,
            }
            for c in cs
        ],
    }


@router.get("/news/events", tags=["news"])
def events(
    start: datetime | None = None,
    end: datetime | None = None,
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> list[dict]:
    q = select(EconomicEventRow).order_by(EconomicEventRow.scheduled_at)
    if start:
        q = q.where(EconomicEventRow.scheduled_at >= start)
    if end:
        q = q.where(EconomicEventRow.scheduled_at < end)
    return [event_out(s, e) for e in s.scalars(q.limit(1000))]


@router.get("/news/provider", tags=["news"])
def news_provider(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    row = s.get(IntegrationConfig, "economic_calendar")
    prov = (row.settings or {}).get("provider", "none") if row else "none"
    return {
        "provider": prov,
        "available": bool(row and row.enabled and row.status == "connected"),
        "origin": "sample" if prov == "sample" else None,
        "detail": row.detail if row else "",
        "last_fetch": get_time_setting(s, "last_calendar_sync"),
    }


# --- integrations & AI -------------------------------------------------------------
@router.get("/integrations", tags=["integrations"])
def integrations(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    return integrations_service.list_integrations(s)


class IntegrationIn(BaseModel):
    enabled: bool = False
    settings: dict[str, Any] = {}
    secrets: dict[str, str] = {}


@router.put("/integrations/{key}", tags=["integrations"])
def put_integration(
    key: str,
    body: IntegrationIn,
    p: Principal = Depends(require_admin),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    return integrations_service.update_integration(s, key, body.enabled, body.settings, body.secrets, p.name)


@router.post("/integrations/{key}/test", tags=["integrations"])
def test_integration(
    key: str, p: Principal = Depends(require_admin), s: Session = Depends(get_db, scope="function")
) -> dict:
    return integrations_service.test_integration(s, key, p.name)


class AnalyzeIn(BaseModel):
    subject: str = Field(pattern="^(backtest|cycle|risk)$")
    id: str = ""


@router.post("/ai/analyze", tags=["ai"])
def analyze(
    body: AnalyzeIn, p: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    return ai_service.analyze(s, body.subject, body.id, p.name)


# --- notifications & logs ----------------------------------------------------------
def _disabled_kinds(s: Session, user_id: str) -> set[str]:
    pref = s.get(NotificationPreference, user_id)
    kinds = (pref.prefs or {}).get("kinds", {}) if pref else {}
    return {k for k, v in kinds.items() if not v.get("in_app", True)}


@router.get("/notifications", tags=["notifications"])
def notifications(
    unread: bool | None = None,
    p: PageParams = Depends(),
    pr: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    q = select(Notification).order_by(Notification.created_at.desc())
    if unread:
        q = q.where(Notification.read_at.is_(None))
    disabled = _disabled_kinds(s, pr.user.id)
    if disabled:
        q = q.where(Notification.kind.notin_(disabled))
    return paginate(s, q, p, notification_out)


@router.post("/notifications/{nid}/read", tags=["notifications"])
def read_one(
    nid: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    n = s.get(Notification, nid)
    if n is None:
        raise not_found("notification")
    n.read_at = n.read_at or utcnow()
    return notification_out(n)


@router.post("/notifications/read-all", tags=["notifications"])
def read_all(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    n = cast(
        CursorResult,
        s.execute(update(Notification).where(Notification.read_at.is_(None)).values(read_at=utcnow())),
    ).rowcount
    return {"updated": n}


@router.get("/notifications/preferences", tags=["notifications"])
def get_prefs(
    p: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    pref = s.get(NotificationPreference, p.user.id)
    kinds = (pref.prefs or {}).get("kinds", {}) if pref else {}
    channels = {
        r.key: {
            "configured": r.status in ("connected", "unverified"),
            "enabled": r.enabled,
            "status": r.status,
        }
        for r in s.scalars(select(IntegrationConfig).where(IntegrationConfig.key.in_(("email", "telegram"))))
    }
    return {
        "kinds": {
            k: {"in_app": kinds.get(k, {}).get("in_app", True), "label": label}
            for k, label in NOTIFY_KINDS.items()
        },
        "channels": {"in_app": {"configured": True, "enabled": True, "status": "connected"}, **channels},
    }


class PrefsIn(BaseModel):
    kinds: dict[str, dict[str, bool]]


@router.put("/notifications/preferences", tags=["notifications"])
def put_prefs(
    body: PrefsIn, p: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    unknown = set(body.kinds) - set(NOTIFY_KINDS)
    if unknown:
        raise bad_request(f"unknown notification kinds: {sorted(unknown)}")
    pref = s.get(NotificationPreference, p.user.id)
    data = {"kinds": {k: {"in_app": bool(v.get("in_app", True))} for k, v in body.kinds.items()}}
    if pref is None:
        s.add(NotificationPreference(user_id=p.user.id, prefs=data))
    else:
        pref.prefs = data
    s.flush()
    return get_prefs(p, s)


@router.get("/logs", tags=["logs"])
def logs(
    severity: str | None = None,
    category: str | None = None,
    bot_id: str | None = None,
    cycle_id: str | None = None,
    correlation_id: str | None = None,
    q: str | None = Query(None, max_length=200),
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    qq = select(AuditLog).order_by(AuditLog.created_at.desc())
    if severity:
        order = ["debug", "info", "warning", "error", "critical"]
        if severity in order:
            qq = qq.where(AuditLog.severity.in_(order[order.index(severity) :]))
    for col, val in (
        (AuditLog.category, category),
        (AuditLog.bot_id, bot_id),
        (AuditLog.cycle_id, cycle_id),
        (AuditLog.correlation_id, correlation_id),
    ):
        if val:
            qq = qq.where(col == val)
    if q:
        qq = qq.where(or_(AuditLog.message.ilike(f"%{q}%")))
    return paginate(s, qq, p, log_out)


@router.get("/logs/{log_id}", tags=["logs"])
def log_detail(
    log_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    a = s.get(AuditLog, log_id)
    if a is None:
        raise not_found("log entry")
    return log_out(a)
