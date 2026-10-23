from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.common import PageParams, bot_names, breaker_out, paginate
from app.auth.deps import Principal, current_principal, require_admin
from app.db.base import get_db, utcnow
from app.models import CircuitBreakerRow, RiskConfigVersion, RiskDecisionRow
from app.services import risk_service
from app.services.broker_service import market_provider
from app.services.dashboard_service import account_view
from app.services.instruments import load_registry
from app.services.system_settings import current_environment
from trading.risk.config import HARD_LIMITS

router = APIRouter(tags=["risk"])


@router.get("/risk/config")
def get_config(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    cfg, row = risk_service.current_risk_config(s)
    return {
        "config": cfg.dump(),
        "hard_limits": HARD_LIMITS,
        "fields": risk_service.risk_fields(),
        "version": row.version if row else 1,
        "updated_at": row.created_at if row else None,
        "updated_by": row.created_by if row else None,
    }


class RiskConfigIn(BaseModel):
    config: dict[str, Any]
    confirm_text: str | None = None


@router.put("/risk/config")
def put_config(
    body: RiskConfigIn, p: Principal = Depends(require_admin), s: Session = Depends(get_db, scope="function")
) -> dict:
    return risk_service.update_risk_config(s, body.config, p.name, body.confirm_text, current_environment(s))


@router.get("/risk/config/history")
def history(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    return [
        {
            "version": r.version,
            "config": r.config,
            "created_at": r.created_at,
            "created_by": r.created_by,
            "changes": r.changes,
        }
        for r in s.scalars(select(RiskConfigVersion).order_by(RiskConfigVersion.version.desc()).limit(100))
    ]


@router.get("/risk/status")
def status(_: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")) -> dict:
    env = current_environment(s)
    reg, _r = load_registry(s)
    acct, _m = account_view(s, env)
    quotes = {}
    if env.value != "live":
        mkt = market_provider(reg)
        for spec in reg.all():
            try:
                quotes[spec.symbol] = mkt.get_quote(spec.symbol, utcnow())
            except Exception:  # noqa: BLE001
                continue
    return risk_service.risk_status(s, env, acct, reg, quotes, utcnow())


@router.get("/risk/decisions")
def decisions(
    approved: bool | None = None,
    bot_id: str | None = None,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    q = (
        select(RiskDecisionRow)
        .where(RiskDecisionRow.environment == current_environment(s).value)
        .order_by(RiskDecisionRow.created_at.desc())
    )
    if approved is not None:
        q = q.where(RiskDecisionRow.approved.is_(approved))
    if bot_id:
        q = q.where(RiskDecisionRow.bot_id == bot_id)
    names = bot_names(s)
    return paginate(
        s,
        q,
        p,
        lambda d: {
            "id": d.id,
            "created_at": d.created_at,
            "bot_id": d.bot_id,
            "bot_name": names.get(d.bot_id, d.bot_id),
            "symbol": d.symbol,
            "approved": d.approved,
            "volume": d.volume,
            "planned_risk": d.planned_risk,
            "planned_risk_pct": d.planned_risk_pct,
            "risk_limit_pct": d.risk_limit_pct,
            "checks": d.checks,
            "reasons": d.reasons,
            "legs": d.legs,
            "cycle_id": d.cycle_id,
        },
    )


@router.get("/risk/breakers")
def breakers(
    active: bool | None = None,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    q = (
        select(CircuitBreakerRow)
        .where(CircuitBreakerRow.environment == current_environment(s).value)
        .order_by(CircuitBreakerRow.activated_at.desc())
    )
    if active is not None:
        q = q.where(CircuitBreakerRow.active.is_(active))
    return paginate(s, q, p, breaker_out)


class ClearIn(BaseModel):
    confirm_text: str | None = None
    note: str = ""


@router.post("/risk/breakers/{breaker_id}/clear")
def clear(
    breaker_id: str,
    body: ClearIn,
    p: Principal = Depends(require_admin),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    return breaker_out(
        risk_service.clear_breaker(s, breaker_id, p.name, p.user.role, body.note, body.confirm_text)
    )
