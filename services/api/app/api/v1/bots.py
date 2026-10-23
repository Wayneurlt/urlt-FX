from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.common import PageParams, cycle_out, log_out, paginate
from app.auth.deps import Principal, current_principal, require_admin, trading_rate_limit
from app.db.base import get_db
from app.models import AuditLog, Bot, BotConfig
from app.services import bots_service, trading_runtime
from app.services.instruments import load_registry
from app.services.system_settings import current_environment

router = APIRouter(tags=["bots"])


@router.get("/bots")
def list_bots(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    env = current_environment(s)
    return [bots_service.bot_out(s, b, env) for b in s.scalars(select(Bot).order_by(Bot.id))]


@router.get("/bots/{bot_id}")
def get_bot(
    bot_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    return bots_service.bot_out(s, bots_service.get_bot(s, bot_id), current_environment(s))


class ActionIn(BaseModel):
    confirm_text: str | None = None


@router.post("/bots/{bot_id}/actions/{action}")
def bot_action(
    bot_id: str,
    action: str,
    body: ActionIn | None = None,
    p: Principal = Depends(trading_rate_limit),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    env = current_environment(s)
    bot = bots_service.apply_action(
        s, bots_service.get_bot(s, bot_id), action, p.name, env, body.confirm_text if body else None
    )
    s.flush()
    return bots_service.bot_out(s, bot, env)


@router.get("/bots/{bot_id}/shutdown-preview")
def shutdown_preview(
    bot_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    prev = trading_runtime.shutdown_preview(s, bot_id)
    names = {b.id: b.name for b in s.scalars(select(Bot))}
    cycles = [cycle_out(c, names) for c in prev["cycles"]]
    positions = [
        {
            "cycle_id": c["id"],
            "leg_id": lg["id"],
            "symbol": c["symbol"],
            "side": lg["side"],
            "volume": lg["filled_volume"],
            "entry_price": lg["fill_price"],
            "stop_loss": lg["confirmed_sl"],
            "floating_pnl": lg["floating_pnl"],
            "simulated": c["simulated"],
        }
        for c in cycles
        for lg in c["legs"]
        if lg["status"] == "open"
    ]
    return {"bot_id": bot_id, "cycles": cycles, "positions": positions, "policy": prev["policy"]}


class ShutdownIn(BaseModel):
    liquidate: bool = False
    confirm_text: str | None = None


@router.post("/bots/{bot_id}/shutdown")
def shutdown(
    bot_id: str,
    body: ShutdownIn,
    p: Principal = Depends(trading_rate_limit),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    res = trading_runtime.shutdown_bot(bot_id, body.liquidate, p.name, body.confirm_text)
    s.expire_all()
    return {
        "bot": bots_service.bot_out(s, bots_service.get_bot(s, bot_id), current_environment(s)),
        "closed_cycles": res["closed_cycles"],
    }


def _config_view(s: Session, bot: Bot) -> dict[str, Any]:
    env = current_environment(s)
    row = bots_service.current_config_row(s, bot.id)
    reg, _ = load_registry(s)
    model = bots_service.config_model(bot.kind)
    return {
        "version": row.version,
        "config": row.config,
        "effective": bots_service.effective_settings(s, bot, reg, env),
        "updated_at": row.created_at,
        "updated_by": row.created_by,
        "schema": model.model_json_schema(),
    }


@router.get("/bots/{bot_id}/config")
def get_config(
    bot_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    return _config_view(s, bots_service.get_bot(s, bot_id))


class ConfigIn(BaseModel):
    config: dict[str, Any]
    confirm_text: str | None = None
    note: str = ""


@router.put("/bots/{bot_id}/config")
def put_config(
    bot_id: str,
    body: ConfigIn,
    p: Principal = Depends(require_admin),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    bot = bots_service.get_bot(s, bot_id)
    bots_service.update_config(
        s, bot, body.config, p.name, current_environment(s), body.confirm_text, body.note
    )
    s.flush()
    return _config_view(s, bot)


@router.get("/bots/{bot_id}/config/history")
def config_history(
    bot_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    rows = s.scalars(select(BotConfig).where(BotConfig.bot_id == bot_id).order_by(BotConfig.version.desc()))
    return [
        {
            "version": r.version,
            "config": r.config,
            "created_at": r.created_at,
            "created_by": r.created_by,
            "note": r.note,
        }
        for r in rows
    ]


@router.get("/bots/{bot_id}/evaluations")
def evaluations(
    bot_id: str,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    bots_service.get_bot(s, bot_id)
    q = (
        select(AuditLog)
        .where(AuditLog.bot_id == bot_id, AuditLog.category == "strategy_evaluation")
        .order_by(AuditLog.created_at.desc())
    )

    def m(a: AuditLog) -> dict:
        data = a.data or {}
        signals, blocked, diags = (
            data.get("signals", []),
            data.get("blocked", []),
            data.get("diagnostics", []),
        )
        symbols = sorted(
            {d.get("symbol") for d in diags if d.get("symbol")}
            | {k.split(":")[2] for k in signals if k.count(":") >= 2}
        )
        modes = sorted({d.get("mode") for d in diags if d.get("mode")})
        result = "signal" if signals else ("blocked" if blocked else "no_signal")
        return {
            "id": log_out(a)["id"],
            "time": a.created_at,
            "symbol": ", ".join(symbols) or "—",
            "mode": ", ".join(modes) or "—",
            "result": result,
            "reason": a.message,
            "details": data,
        }

    out = paginate(s, q, p, m)
    bot = s.get(Bot, bot_id)
    out["latest"] = bot.last_evaluation if bot else None
    return out


@router.post("/bots/{bot_id}/evaluate-now")
def evaluate_now(bot_id: str, p: Principal = Depends(trading_rate_limit)) -> dict:
    return trading_runtime.evaluate_now(bot_id, p.name)
