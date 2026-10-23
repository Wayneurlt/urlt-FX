from __future__ import annotations

import csv
import dataclasses
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.api.v1.common import PageParams, bot_names, cycle_out, log_out, paginate, trade_row_out
from app.auth.deps import Principal, current_principal, trading_rate_limit
from app.core.errors import not_found
from app.db.base import get_db, utcnow
from app.models import AuditLog, PositionLeg, TradingCycle
from app.services import dashboard_service, trading_runtime
from app.services.broker_service import market_provider
from app.services.instruments import load_registry
from app.services.system_settings import current_environment

router = APIRouter(tags=["trading"])


@router.get("/positions")
def positions(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    env = current_environment(s)
    names = bot_names(s)
    reg, _r = load_registry(s)
    mkt = market_provider(reg) if env.value != "live" else None
    out = []
    q = (
        select(PositionLeg)
        .join(TradingCycle)
        .where(PositionLeg.status == "open", TradingCycle.environment == env.value)
        .options(selectinload(PositionLeg.cycle))
    )
    for lg in s.scalars(q):
        c = lg.cycle
        price = None
        if mkt is not None:
            try:
                qt = mkt.get_quote(c.symbol, utcnow())
                price = qt.bid if lg.side == "BUY" else qt.ask
            except Exception:  # noqa: BLE001
                price = None
        out.append(
            {
                "cycle_id": c.id,
                "leg_id": lg.id,
                "bot_id": c.bot_id,
                "bot_name": names.get(c.bot_id, c.bot_id),
                "symbol": c.symbol,
                "side": lg.side,
                "volume": lg.filled_volume,
                "entry_price": lg.fill_price,
                "current_price": price,
                "stop_loss": lg.confirmed_sl,
                "take_profit": lg.tp or lg.intended_tp,
                "floating_pnl": lg.floating_pnl,
                "opened_at": lg.fill_time,
                "trailing_active": c.trailing_activated_at is not None
                or bool((c.management or {}).get("trailing")),
                "simulated": c.simulated,
                "environment": c.environment,
                "position_id": lg.position_id,
            }
        )
    return out


@router.get("/cycles")
def cycles(
    bot_id: str | None = None,
    state: str | None = None,
    active: bool | None = None,
    symbol: str | None = None,
    environment: str | None = None,
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    q = select(TradingCycle).options(selectinload(TradingCycle.legs)).order_by(TradingCycle.created_at.desc())
    if bot_id:
        q = q.where(TradingCycle.bot_id == bot_id)
    if state:
        q = q.where(TradingCycle.state == state)
    if active is not None:
        q = q.where(TradingCycle.active.is_(active))
    if symbol:
        q = q.where(TradingCycle.symbol == symbol)
    q = q.where(TradingCycle.environment == (environment or current_environment(s).value))
    names = bot_names(s)
    return paginate(s, q, p, lambda c: cycle_out(c, names))


@router.get("/cycles/{cycle_id}")
def cycle_detail(
    cycle_id: str, _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    c = s.get(TradingCycle, cycle_id)
    if c is None:
        raise not_found("cycle")
    events = s.scalars(
        select(AuditLog).where(AuditLog.cycle_id == cycle_id).order_by(AuditLog.created_at)
    ).all()
    return {
        **cycle_out(c, bot_names(s)),
        "risk_decision": c.risk_decision,
        "signal": c.signal,
        "config_snapshot": c.config_snapshot,
        "trailing": c.trailing,
        "management": c.management,
        "events": [log_out(e) for e in events],
    }


class CloseIn(BaseModel):
    confirm_text: str | None = None


@router.post("/cycles/{cycle_id}/close")
def close_cycle(cycle_id: str, body: CloseIn, p: Principal = Depends(trading_rate_limit)) -> dict:
    return trading_runtime.liquidate_cycle(cycle_id, p.name, body.confirm_text)


def _trade_query(
    s: Session,
    start: datetime | None,
    end: datetime | None,
    bot_id: str | None,
    strategy_version: str | None,
    symbol: str | None,
    side: str | None,
    environment: str | None,
    trigger: str | None,
    outcome: str | None,
    status: str | None,
    cycle_id: str | None,
    sort: str,
) -> Select:
    q = (
        select(PositionLeg)
        .join(TradingCycle)
        .options(selectinload(PositionLeg.cycle))
        .where(PositionLeg.filled_volume > 0)
    )
    if start:
        q = q.where(PositionLeg.fill_time >= start)
    if end:
        q = q.where(PositionLeg.fill_time < end)
    for col, val in (
        (TradingCycle.bot_id, bot_id),
        (TradingCycle.strategy_version, strategy_version),
        (TradingCycle.symbol, symbol),
        (PositionLeg.side, side),
        (TradingCycle.trigger_type, trigger),
        (PositionLeg.status, status),
        (TradingCycle.id, cycle_id),
    ):
        if val:
            q = q.where(col == val)
    q = q.where(TradingCycle.environment == (environment or current_environment(s).value))
    if outcome == "win":
        q = q.where(PositionLeg.status == "closed", PositionLeg.net_pnl > 0)
    elif outcome == "loss":
        q = q.where(PositionLeg.status == "closed", PositionLeg.net_pnl <= 0)
    elif outcome == "open":
        q = q.where(PositionLeg.status == "open")
    sort_col: Any = {
        "fill_time": PositionLeg.fill_time,
        "exit_time": PositionLeg.exit_time,
        "net_pnl": PositionLeg.net_pnl,
    }.get(sort.lstrip("-"), PositionLeg.fill_time)
    return q.order_by(sort_col.desc() if sort.startswith("-") else sort_col.asc())


@router.get("/trades")
def trades(
    start: datetime | None = None,
    end: datetime | None = None,
    bot_id: str | None = None,
    strategy_version: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    environment: str | None = None,
    trigger: str | None = None,
    outcome: str | None = None,
    status: str | None = None,
    cycle_id: str | None = None,
    sort: str = Query("-fill_time"),
    p: PageParams = Depends(),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    q = _trade_query(
        s,
        start,
        end,
        bot_id,
        strategy_version,
        symbol,
        side,
        environment,
        trigger,
        outcome,
        status,
        cycle_id,
        sort,
    )
    names = bot_names(s)
    return paginate(s, q, p, lambda lg: trade_row_out(lg, names))


@router.get("/trades/export.csv", response_class=PlainTextResponse)
def trades_csv(
    start: datetime | None = None,
    end: datetime | None = None,
    bot_id: str | None = None,
    strategy_version: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    environment: str | None = None,
    trigger: str | None = None,
    outcome: str | None = None,
    status: str | None = None,
    cycle_id: str | None = None,
    sort: str = Query("-fill_time"),
    _: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> PlainTextResponse:
    q = _trade_query(
        s,
        start,
        end,
        bot_id,
        strategy_version,
        symbol,
        side,
        environment,
        trigger,
        outcome,
        status,
        cycle_id,
        sort,
    ).limit(100_000)
    names = bot_names(s)
    cols = [
        "cycle_id",
        "bot_name",
        "symbol",
        "side",
        "simulated",
        "environment",
        "trigger_type",
        "strategy_version",
        "client_order_id",
        "position_id",
        "filled_volume",
        "fill_time",
        "fill_price",
        "intended_sl",
        "confirmed_sl",
        "exit_time",
        "exit_price",
        "exit_reason",
        "gross_pnl",
        "commission",
        "swap",
        "net_pnl",
        "slippage_pips",
        "status",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for lg in s.scalars(q):
        w.writerow(trade_row_out(lg, names))
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


class ManualEntryIn(BaseModel):
    bot_id: str
    symbol: str
    side: str | None = Field(None, pattern="^(BUY|SELL)$")
    stop_pips: float = Field(gt=0, le=1000)
    target_pips: float | None = Field(None, gt=0, le=5000)


@router.post("/paper/manual-entry")
def manual_entry(body: ManualEntryIn, p: Principal = Depends(trading_rate_limit)) -> dict:
    return trading_runtime.manual_entry(
        body.bot_id, body.symbol, body.side, body.stop_pips, body.target_pips, p.name
    )


@router.get("/paper/account")
def paper_account(
    _: Principal = Depends(current_principal), s: Session = Depends(get_db, scope="function")
) -> dict:
    from app.models import SimBrokerState

    env = current_environment(s)
    acct, meta = dashboard_service.account_view(s, env)
    row = s.get(SimBrokerState, env.value)
    return {
        "environment": env.value,
        "account": dataclasses.asdict(acct) if acct else None,
        **meta,
        "reset_at": row.reset_at if row else None,
        "settings": row.settings if row else None,
    }


class ResetIn(BaseModel):
    initial_balance: float = Field(gt=0)
    confirm_text: str | None = None


@router.post("/paper/reset")
def paper_reset(body: ResetIn, p: Principal = Depends(trading_rate_limit)) -> dict:
    return trading_runtime.reset_paper_account(body.initial_balance, p.name, body.confirm_text)
