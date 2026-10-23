"""Read models for the overview dashboard. Everything is computed from stored
records - nothing here is invented. Metrics without enough data are null."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    AccountSnapshot,
    Bot,
    EconomicEventRow,
    Notification,
    OrderIntent,
    PositionLeg,
    RiskState,
    SimBrokerState,
    TradingCycle,
    TradingSignal,
)
from app.services.instruments import load_registry
from app.services.risk_service import active_breaker_rows
from trading.adapters.base import AccountInfo
from trading.adapters.simulated_broker import SimulatedBroker, sim_config_from_dict
from trading.interfaces.types import Environment
from trading.risk.daily import drawdown_pct, max_drawdown


def account_view(s: Session, env: Environment) -> tuple[AccountInfo | None, dict[str, Any]]:
    """Current account. Simulated environments compute it from the persisted simulator state."""
    if env in (Environment.SIMULATION, Environment.PAPER):
        row = s.get(SimBrokerState, env.value)
        if row is None:
            return None, {
                "simulated": True,
                "data_source": "synthetic",
                "label": "Simulated paper account (not started yet)",
                "as_of": None,
            }
        from app.services.broker_service import market_provider

        reg, _ = load_registry(s)
        sim = SimulatedBroker(
            market_provider(reg), utcnow, sim_config_from_dict(row.settings), reg, row.state
        )
        acct = sim.account_info()
        return acct, {
            "simulated": True,
            "data_source": "synthetic",
            "as_of": utcnow(),
            "label": f"SIMULATED {env.value} account - simulated execution on synthetic prices",
        }
    snap = s.scalars(
        select(AccountSnapshot)
        .where(AccountSnapshot.environment == env.value)
        .order_by(AccountSnapshot.time.desc())
        .limit(1)
    ).first()
    if snap is None:
        return None, {
            "simulated": False,
            "data_source": "live",
            "label": "No live account data received yet",
            "as_of": None,
        }
    from trading.interfaces.types import MarginMode

    acct = AccountInfo(
        "live",
        snap.currency,
        snap.balance,
        snap.equity,
        snap.margin,
        snap.free_margin,
        snap.margin_level,
        0.0,
        MarginMode.UNKNOWN,
        False,
        False,
    )
    return acct, {
        "simulated": False,
        "data_source": "live",
        "label": "Live broker account (last snapshot)",
        "as_of": snap.time,
    }


def _since_reset(s: Session, env: Environment) -> datetime | None:
    row = s.get(SimBrokerState, env.value)
    return row.reset_at if row else None


def _closed_cycles(
    s: Session, env: Environment, since: datetime | None = None, until: datetime | None = None
) -> list[TradingCycle]:
    q = select(TradingCycle).where(
        TradingCycle.environment == env.value,
        TradingCycle.closed_at.is_not(None),
        TradingCycle.active.is_(False),
    )
    reset = _since_reset(s, env)
    lo = max([t for t in (since, reset) if t is not None], default=None)
    if lo is not None:
        q = q.where(TradingCycle.closed_at >= lo)
    if until is not None:
        q = q.where(TradingCycle.closed_at < until)
    return list(s.scalars(q.order_by(TradingCycle.closed_at)))


def summary(s: Session, env: Environment) -> dict[str, Any]:
    now = utcnow()
    acct, meta = account_view(s, env)
    state = s.get(RiskState, env.value)
    day_start = (
        state.day_start_at
        if state and state.day_start_at
        else now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    cycles = _closed_cycles(s, env)
    filled = [c for c in cycles if any(lg.filled_volume > 0 for lg in c.legs)]
    legs = [lg for c in filled for lg in c.legs if lg.status == "closed"]
    nets = [lg.net_pnl for lg in legs]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]

    def pnl_since(t: datetime) -> float:
        return round(sum(c.realized_pnl for c in filled if c.closed_at and c.closed_at >= t), 2)

    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    insufficient = []
    win_rate = round(len(wins) / len(nets), 4) if nets else None
    pf = round(sum(wins) / abs(sum(losses)), 3) if wins and losses and sum(losses) != 0 else None
    if not nets:
        insufficient += ["win_rate", "average_trade"]
    if pf is None:
        insufficient.append("profit_factor")
    snaps = [
        x.equity
        for x in s.scalars(
            select(AccountSnapshot)
            .where(
                AccountSnapshot.environment == env.value,
                AccountSnapshot.time >= (_since_reset(s, env) or datetime.min.replace(tzinfo=now.tzinfo)),
            )
            .order_by(AccountSnapshot.time)
        )
    ]
    mdd_pct = round(max_drawdown(snaps)[1], 3) if len(snaps) >= 2 else None
    if mdd_pct is None:
        insufficient.append("max_drawdown_pct")
    initial = None
    if env is not Environment.LIVE:
        row = s.get(SimBrokerState, env.value)
        initial = float((row.settings or {}).get("initial_balance", 0)) if row else None
    total_return = round((acct.equity - initial) / initial * 100, 3) if acct and initial else None
    if total_return is None:
        insufficient.append("total_return_pct")

    realized_total = round(sum(c.realized_pnl for c in filled), 2)
    account = None
    if acct:
        account = {
            "currency": acct.currency,
            "balance": acct.balance,
            "equity": acct.equity,
            "floating_pnl": round(acct.equity - acct.balance, 2),
            "realized_pnl": realized_total,
            "daily_pnl": round(acct.equity - state.day_start_equity, 2)
            if state and state.day_start_equity
            else pnl_since(day_start),
            "drawdown_pct": round(drawdown_pct(state.peak_equity, acct.equity), 3)
            if state and state.peak_equity
            else None,
            "margin": acct.margin,
            "free_margin": acct.free_margin,
            "margin_level": acct.margin_level,
            **meta,
        }
    open_positions = (
        s.scalar(
            select(func.count())
            .select_from(PositionLeg)
            .join(TradingCycle)
            .where(PositionLeg.status == "open", TradingCycle.environment == env.value)
        )
        or 0
    )
    active_cycles = (
        s.scalar(
            select(func.count())
            .select_from(TradingCycle)
            .where(TradingCycle.active.is_(True), TradingCycle.environment == env.value)
        )
        or 0
    )
    bots = [
        {
            "id": b.id,
            "name": b.name,
            "kind": b.kind,
            "status": b.status,
            "daily_pnl": round(
                sum(
                    c.realized_pnl
                    for c in filled
                    if c.bot_id == b.id and c.closed_at and c.closed_at >= day_start
                ),
                2,
            ),
        }
        for b in s.scalars(select(Bot).order_by(Bot.id))
    ]
    alerts = list(
        s.scalars(
            select(Notification)
            .where(Notification.severity.in_(("warning", "error", "critical")))
            .order_by(Notification.created_at.desc())
            .limit(8)
        )
    )
    bot_names = {b["id"]: b["name"] for b in bots}
    execs = []
    for oi, cyc in s.execute(
        select(OrderIntent, TradingCycle)
        .join(TradingCycle, OrderIntent.cycle_id == TradingCycle.id)
        .where(TradingCycle.environment == env.value)
        .order_by(OrderIntent.updated_at.desc())
        .limit(10)
    ):
        kind = (
            "rejected"
            if oi.intent_status in ("rejected", "not_executed")
            else ("fill" if oi.fill_price else oi.intent_status)
        )
        execs.append(
            {
                "time": oi.updated_at,
                "cycle_id": cyc.id,
                "bot_name": bot_names.get(cyc.bot_id, cyc.bot_id),
                "symbol": cyc.symbol,
                "side": oi.side,
                "kind": kind,
                "price": oi.fill_price,
                "volume": oi.filled_volume,
                "detail": oi.reject_reason or oi.intent_status,
                "simulated": oi.simulated,
            }
        )
    events = list(
        s.scalars(
            select(EconomicEventRow)
            .where(
                EconomicEventRow.scheduled_at >= now, EconomicEventRow.scheduled_at <= now + timedelta(days=2)
            )
            .order_by(EconomicEventRow.scheduled_at)
            .limit(10)
        )
    )
    from app.api.v1.common import breaker_out, event_out, notification_out
    from app.services.health_service import health_checks

    return {
        "account": account,
        "performance": {
            "today_pnl": pnl_since(day_start),
            "week_pnl": pnl_since(week_start),
            "month_pnl": pnl_since(month_start),
            "total_return_pct": total_return,
            "max_drawdown_pct": mdd_pct,
            "win_rate": win_rate,
            "profit_factor": pf,
            "average_trade": round(sum(nets) / len(nets), 2) if nets else None,
            "completed_trades": len(legs),
            "completed_cycles": len(filled),
            "insufficient": insufficient,
        },
        "open_positions": int(open_positions),
        "active_cycles": int(active_cycles),
        "bots": bots,
        "active_breakers": [breaker_out(b) for b in active_breaker_rows(s, env)],
        "recent_alerts": [notification_out(n) for n in alerts],
        "recent_executions": execs,
        "upcoming_events": [event_out(s, e) for e in events],
        "health": health_checks(s),
    }


def charts(s: Session, env: Environment, start: datetime | None, end: datetime | None) -> dict[str, Any]:
    reset = _since_reset(s, env)
    lo = max([t for t in (start, reset) if t is not None], default=None)
    q = select(AccountSnapshot).where(AccountSnapshot.environment == env.value)
    if lo:
        q = q.where(AccountSnapshot.time >= lo)
    if end:
        q = q.where(AccountSnapshot.time < end)
    snaps = list(s.scalars(q.order_by(AccountSnapshot.time)))
    step = max(1, len(snaps) // 1500)
    snaps_ds = snaps[::step] + ([snaps[-1]] if snaps and snaps[-1] is not snaps[::step][-1] else [])
    peak = float("-inf")
    dd = []
    for x in snaps_ds:
        peak = max(peak, x.equity)
        dd.append(
            {"time": x.time, "drawdown_pct": round((peak - x.equity) / peak * 100, 3) if peak > 0 else 0.0}
        )
    cycles = _closed_cycles(s, env, start, end)
    daily: dict[str, float] = defaultdict(float)
    by_bot: dict[str, list[float]] = defaultdict(list)
    by_sym: dict[str, list[float]] = defaultdict(list)
    for c in cycles:
        if c.closed_at is None or not any(lg.filled_volume > 0 for lg in c.legs):
            continue
        daily[c.closed_at.date().isoformat()] += c.realized_pnl
        by_bot[c.bot_id].append(c.realized_pnl)
        for lg in c.legs:
            if lg.status == "closed":
                by_sym[c.symbol].append(lg.net_pnl)
    names = {b.id: b.name for b in s.scalars(select(Bot))}
    recent = [c for c in reversed(cycles) if any(lg.filled_volume > 0 for lg in c.legs)][:20]
    return {
        "equity": [{"time": x.time, "equity": x.equity, "balance": x.balance} for x in snaps_ds],
        "daily_pnl": [{"date": d, "pnl": round(v, 2)} for d, v in sorted(daily.items())],
        "drawdown": dd,
        "by_bot": [
            {"bot_id": b, "name": names.get(b, b), "net_pnl": round(sum(v), 2), "cycles": len(v)}
            for b, v in sorted(by_bot.items())
        ],
        "by_instrument": [
            {"symbol": k, "net_pnl": round(sum(v), 2), "trades": len(v)} for k, v in sorted(by_sym.items())
        ],
        "recent_outcomes": [
            {
                "cycle_id": c.id,
                "bot_name": names.get(c.bot_id, c.bot_id),
                "symbol": c.symbol,
                "net_pnl": c.realized_pnl,
                "closed_at": c.closed_at,
                "outcome": c.outcome,
            }
            for c in recent
        ],
        "simulated": env is not Environment.LIVE,
    }


def processed_event_ids(s: Session) -> set[str]:
    return {
        e for e in s.scalars(select(TradingSignal.event_id).where(TradingSignal.event_id.is_not(None))) if e
    }
