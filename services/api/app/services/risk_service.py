from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.errors import APIError, bad_request, not_found, require_confirm
from app.db.base import utcnow
from app.models import (
    Bot,
    CircuitBreakerRow,
    RiskConfigVersion,
    RiskDecisionRow,
    RiskReservation,
    RiskState,
    TradingCycle,
)
from app.services.audit import audit, notify
from trading.adapters.base import AccountInfo
from trading.instruments.conversion import RateBook
from trading.instruments.spec import InstrumentRegistry, InstrumentSpec
from trading.interfaces.types import BotKind, Environment
from trading.risk.circuit_breakers import BREAKER_META, ActiveBreaker, BreakerScope, BreakerType
from trading.risk.config import (
    HARD_LIMITS,
    DailyBotRiskConfig,
    EnvironmentLimits,
    GlobalRiskConfig,
    NewsBotRiskConfig,
    RiskConfig,
    risk_increases,
)
from trading.risk.daily import daily_loss, daily_reference_start, drawdown_pct
from trading.risk.engine import OpenRiskItem, PortfolioSnapshot, RiskDecision, RiskEngine, TradeProposal

RISK_INCREASE_PHRASE = "CONFIRM RISK INCREASE"


# --- configuration -------------------------------------------------------------
def current_risk_config(s: Session) -> tuple[RiskConfig, RiskConfigVersion | None]:
    row = s.scalars(select(RiskConfigVersion).order_by(RiskConfigVersion.version.desc()).limit(1)).first()
    if row is None:
        return RiskConfig(), None
    return RiskConfig.model_validate(row.config), row


def seed_risk_config(s: Session) -> None:
    if s.scalars(select(RiskConfigVersion).limit(1)).first() is None:
        s.add(RiskConfigVersion(version=1, config=RiskConfig().dump(), changes=[], created_by="system"))


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        p = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, p))
        else:
            out[p] = v
    return out


def update_risk_config(
    s: Session, new: dict[str, Any], actor: str, confirm_text: str | None, environment: Environment
) -> dict[str, Any]:
    before, row = current_risk_config(s)
    try:
        after = RiskConfig.model_validate(new)
    except ValidationError as exc:
        raise bad_request(
            "invalid risk configuration",
            "validation_error",
            errors=[{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()],
        ) from exc
    b, a = _flatten(before.dump()), _flatten(after.dump())
    changes = [
        {"path": k, "before": b.get(k), "after": a.get(k)}
        for k in sorted(set(b) | set(a))
        if b.get(k) != a.get(k)
    ]
    if not changes:
        return {"config": before.dump(), "changes": [], "loosened": [], "version": row.version if row else 1}
    loosened = risk_increases(before, after)
    if loosened:
        require_confirm(
            confirm_text,
            RISK_INCREASE_PHRASE,
            "this change loosens risk limits and needs explicit confirmation",
            loosened=loosened,
            changes=changes,
        )
    elif environment is Environment.LIVE:
        require_confirm(
            confirm_text, "CONFIRM", "risk changes in live mode need confirmation", changes=changes
        )
    version = (row.version if row else 0) + 1
    s.add(RiskConfigVersion(version=version, config=after.dump(), changes=changes, created_by=actor))
    audit(
        s,
        "configuration",
        f"risk configuration v{version} saved ({len(changes)} changes; loosened: {loosened or 'none'})",
        "warning" if loosened else "info",
        actor=actor,
        data={"changes": changes, "loosened": loosened},
    )
    return {"config": after.dump(), "changes": changes, "loosened": loosened, "version": version}


_UNITS = {"pct": "%", "seconds": "seconds", "pips": "pips"}


def _unit_for(name: str, annotation: Any) -> str:
    if name.endswith("_pct"):
        return "%"
    if "seconds" in name:
        return "seconds"
    if "pips" in name:
        return "pips"
    if name.endswith("_hour_utc"):
        return "hour (UTC)"
    if "policy" in name:
        return "choice"
    if annotation is int:
        return "count"
    return ""


def risk_fields() -> list[dict[str, Any]]:
    out = []
    for section, model in (
        ("global", GlobalRiskConfig),
        ("news_volatility", NewsBotRiskConfig),
        ("daily_trading", DailyBotRiskConfig),
    ):
        assert issubclass(model, BaseModel)
        for name, f in model.model_fields.items():
            lo = hi = None
            for m in f.metadata:
                lo = (
                    getattr(m, "ge", None)
                    if getattr(m, "ge", None) is not None
                    else (getattr(m, "gt", None) if getattr(m, "gt", None) is not None else lo)
                )
                hi = (
                    getattr(m, "le", None)
                    if getattr(m, "le", None) is not None
                    else (getattr(m, "lt", None) if getattr(m, "lt", None) is not None else hi)
                )
            choices = list(getattr(f.annotation, "__args__", ())) if "policy" in name else None
            out.append(
                {
                    "path": f"{section}.{name}",
                    "section": section,
                    "label": name.replace("_", " ").replace(" pct", " %").capitalize(),
                    "description": f.description or "",
                    "unit": _unit_for(name, f.annotation),
                    "default": f.default,
                    "min": lo,
                    "max": hi,
                    "choices": choices,
                    "nullable": False,
                }
            )
    # Per-environment caps: the tighter of these and the bot settings applies. Empty = no extra cap.
    for env in (Environment.SIMULATION, Environment.PAPER, Environment.LIVE):
        for name, f in EnvironmentLimits.model_fields.items():
            hi = next((m.le for m in f.metadata if getattr(m, "le", None) is not None), None)
            label = {
                "max_cycle_risk_pct": "max cycle risk %",
                "max_trade_risk_pct": "max trade risk %",
                "max_daily_loss_pct": "max daily loss %",
            }.get(name, name.replace("_", " "))
            out.append(
                {
                    "path": f"environments.{env.value}.{name}",
                    "section": "environments",
                    "label": f"{env.value.capitalize()}: {label}",
                    "description": f"Cap applied only in {env.value} mode on top of the bot settings (the tighter wins). Leave empty for no extra cap.",
                    "unit": "%",
                    "default": RiskConfig().env(env).model_dump().get(name),
                    "min": 0.01,
                    "max": hi,
                    "choices": None,
                    "nullable": True,
                }
            )
    return out


# --- circuit breakers ----------------------------------------------------------
def active_breaker_rows(s: Session, env: Environment) -> list[CircuitBreakerRow]:
    return list(
        s.scalars(
            select(CircuitBreakerRow)
            .where(CircuitBreakerRow.active.is_(True), CircuitBreakerRow.environment == env.value)
            .order_by(CircuitBreakerRow.activated_at.desc())
        )
    )


def active_breakers(s: Session, env: Environment) -> list[ActiveBreaker]:
    return [
        ActiveBreaker(BreakerType(r.type), BreakerScope(r.scope), r.scope_key, r.reason)
        for r in active_breaker_rows(s, env)
    ]


# Data blips (one quiet pair, one spread spike) block entries immediately but only notify the
# user if they last: otherwise every quiet second on a Friday evening becomes an alert.
BLIP_TYPES = frozenset({BreakerType.STALE_MARKET_DATA, BreakerType.EXCESSIVE_SPREAD})
BLIP_NOTIFY_AFTER = timedelta(seconds=60)


def trip_breaker(
    s: Session,
    env: Environment,
    type: BreakerType,
    scope: BreakerScope,
    scope_key: str | None,
    reason: str,
    details: dict | None = None,
) -> CircuitBreakerRow:
    existing = s.scalars(
        select(CircuitBreakerRow).where(
            CircuitBreakerRow.active.is_(True),
            CircuitBreakerRow.environment == env.value,
            CircuitBreakerRow.type == type.value,
            CircuitBreakerRow.scope == scope.value,
            CircuitBreakerRow.scope_key.is_(None)
            if scope_key is None
            else CircuitBreakerRow.scope_key == scope_key,
        )
    ).first()
    if existing:
        return existing
    meta = BREAKER_META[type]
    row = CircuitBreakerRow(
        environment=env.value,
        type=type.value,
        scope=scope.value,
        scope_key=scope_key,
        reason=reason,
        critical=meta.critical,
        auto_clear=meta.auto_clear,
        recovery=meta.recovery,
        details={**(details or {}), "notified": type not in BLIP_TYPES},
    )
    s.add(row)
    s.flush()
    blip = type in BLIP_TYPES
    sev = "critical" if meta.critical else ("info" if blip else "warning")
    audit(
        s,
        "risk",
        f"circuit breaker {type.value} ({scope.value}{':' + scope_key if scope_key else ''}): {reason}",
        sev,
        bot_id=scope_key if scope is BreakerScope.BOT else None,
        environment=env.value,
        data={"breaker_id": row.id},
    )
    kind = (
        "risk_limit_reached"
        if type in (BreakerType.DAILY_LOSS_LIMIT, BreakerType.MAX_DRAWDOWN)
        else ("broker_disconnected" if type is BreakerType.BROKER_DISCONNECTED else "circuit_breaker")
    )
    if not blip:
        notify(
            s,
            kind,
            f"{type.value.replace('_', ' ')}: {reason}",
            sev,
            bot_id=scope_key if scope is BreakerScope.BOT else None,
        )
    return row


def notify_lingering_blips(s: Session, env: Environment, now: datetime) -> int:
    """Notify once for any data-blip breaker that has stayed active longer than BLIP_NOTIFY_AFTER."""
    n = 0
    rows = s.scalars(
        select(CircuitBreakerRow).where(
            CircuitBreakerRow.active.is_(True),
            CircuitBreakerRow.environment == env.value,
            CircuitBreakerRow.type.in_([t.value for t in BLIP_TYPES]),
            CircuitBreakerRow.activated_at <= now - BLIP_NOTIFY_AFTER,
        )
    )
    for row in rows:
        if (row.details or {}).get("notified"):
            continue
        secs = int((now - row.activated_at).total_seconds())
        notify(
            s,
            "circuit_breaker",
            f"{row.type.replace('_', ' ')} on {row.scope_key} for {secs}s: {row.reason}",
            "warning",
        )
        row.details = {**(row.details or {}), "notified": True}
        n += 1
    return n


def auto_clear(
    s: Session,
    env: Environment,
    type: BreakerType,
    scope_key: str | None = None,
    note: str = "condition resolved",
) -> int:
    q = select(CircuitBreakerRow).where(
        CircuitBreakerRow.active.is_(True),
        CircuitBreakerRow.environment == env.value,
        CircuitBreakerRow.type == type.value,
        CircuitBreakerRow.auto_clear.is_(True),
    )
    if scope_key is not None:
        q = q.where(CircuitBreakerRow.scope_key == scope_key)
    n = 0
    for row in s.scalars(q):
        row.active = False
        row.cleared_at = utcnow()
        row.cleared_by = "system"
        row.clear_note = note
        audit(s, "risk", f"circuit breaker {row.type} auto-cleared: {note}", environment=env.value)
        n += 1
    return n


def clear_breaker(
    s: Session, breaker_id: str, actor: str, role: str, note: str, confirm_text: str | None
) -> CircuitBreakerRow:
    row = s.get(CircuitBreakerRow, breaker_id)
    if row is None:
        raise not_found("circuit breaker")
    if not row.active:
        raise APIError(409, "already_cleared", "circuit breaker is not active")
    require_confirm(
        confirm_text, "CLEAR", "clearing a circuit breaker re-enables new entries", breaker_type=row.type
    )
    if row.critical and role != "admin":
        raise APIError(403, "forbidden", "critical breakers can only be cleared by an administrator")
    if not note.strip():
        raise bad_request("a note explaining why the breaker can be cleared is required")
    row.active = False
    row.cleared_at = utcnow()
    row.cleared_by = actor
    row.clear_note = note
    audit(
        s,
        "risk",
        f"circuit breaker {row.type} cleared manually: {note}",
        "warning",
        actor=actor,
        environment=row.environment,
        data={"breaker_id": row.id},
    )
    return row


# --- portfolio ---------------------------------------------------------------------
def risk_state_row(s: Session, env: Environment) -> RiskState:
    row = s.get(RiskState, env.value)
    if row is None:
        row = RiskState(environment=env.value, version=0)
        s.add(row)
        s.flush()
    return row


def update_risk_state(
    s: Session, env: Environment, account: AccountInfo, now: datetime, cfg: RiskConfig
) -> RiskState:
    row = risk_state_row(s, env)
    start = daily_reference_start(now, cfg.global_.daily_reset_hour_utc)
    if row.day_start_at is None or row.day_start_at < start or row.day_start_equity is None:
        rollover = row.day_start_at is not None and row.day_start_at < start
        row.day_start_at = start
        row.day_start_equity = account.equity
        # Daily-reset breakers clear only at a real day rollover, never on first initialisation.
        for t in () if not rollover else (BreakerType.DAILY_LOSS_LIMIT, BreakerType.CONSECUTIVE_LOSSES):
            for b in s.scalars(
                select(CircuitBreakerRow).where(
                    CircuitBreakerRow.active.is_(True),
                    CircuitBreakerRow.environment == env.value,
                    CircuitBreakerRow.type == t.value,
                )
            ):
                b.active = False
                b.cleared_at = now
                b.cleared_by = "system"
                b.clear_note = "daily reset"
                audit(s, "risk", f"{t.value} breaker cleared at daily reset", environment=env.value)
    row.peak_equity = max(row.peak_equity or account.equity, account.equity)
    row.updated_at = now
    return row


def consecutive_losses(s: Session, env: Environment, bot_id: str, since: datetime | None) -> int:
    last_clear = s.scalar(
        select(func.max(CircuitBreakerRow.cleared_at)).where(
            CircuitBreakerRow.type == BreakerType.CONSECUTIVE_LOSSES.value,
            CircuitBreakerRow.scope_key == bot_id,
            CircuitBreakerRow.environment == env.value,
        )
    )
    lo = max([t for t in (since, last_clear) if t is not None], default=None)
    q = select(TradingCycle.realized_pnl).where(
        TradingCycle.bot_id == bot_id,
        TradingCycle.environment == env.value,
        TradingCycle.active.is_(False),
        TradingCycle.closed_at.is_not(None),
        TradingCycle.state.in_(("completed", "closed")),
    )
    if lo is not None:
        q = q.where(TradingCycle.closed_at >= lo)
    n = 0
    for pnl in s.scalars(q.order_by(TradingCycle.closed_at.desc()).limit(50)):
        if pnl <= 0:
            n += 1
        else:
            break
    return n


def bot_realized_since(s: Session, env: Environment, since: datetime | None) -> dict[str, float]:
    q = select(TradingCycle.bot_id, func.coalesce(func.sum(TradingCycle.realized_pnl), 0.0)).where(
        TradingCycle.environment == env.value, TradingCycle.closed_at.is_not(None)
    )
    if since is not None:
        q = q.where(TradingCycle.closed_at >= since)
    return {b: float(v) for b, v in s.execute(q.group_by(TradingCycle.bot_id))}


def open_items(s: Session, env: Environment) -> list[OpenRiskItem]:
    rows = s.scalars(
        select(RiskReservation).where(
            RiskReservation.active.is_(True), RiskReservation.environment == env.value
        )
    )
    return [
        OpenRiskItem(
            r.bot_id,
            BotKind(r.bot_kind),
            r.cycle_id,
            r.symbol,
            r.base_currency,
            r.quote_currency,
            r.planned_risk,
            r.correlation_group,
        )
        for r in rows
    ]


def portfolio_snapshot(
    s: Session, env: Environment, account: AccountInfo, cfg: RiskConfig, now: datetime
) -> PortfolioSnapshot:
    state = update_risk_state(s, env, account, now, cfg)
    bots = [b.id for b in s.scalars(select(Bot))]
    return PortfolioSnapshot(
        account_currency=account.currency,
        equity=account.equity,
        balance=account.balance,
        day_start_equity=state.day_start_equity or account.equity,
        peak_equity=state.peak_equity or account.equity,
        free_margin=account.free_margin,
        used_margin=account.margin,
        open_items=open_items(s, env),
        active_breakers=active_breakers(s, env),
        consecutive_losses={b: consecutive_losses(s, env, b, state.day_start_at) for b in bots},
        bot_realized_today=bot_realized_since(s, env, state.day_start_at),
    )


def evaluate_and_reserve(
    s: Session,
    env: Environment,
    cfg: RiskConfig,
    proposal: TradeProposal,
    pf: PortfolioSnapshot,
    spec: InstrumentSpec | None,
    rates: RateBook | None,
    cycle_id: str,
    signal_key: str,
) -> RiskDecision:
    """Evaluate a proposal and, if approved, reserve its risk atomically.

    The reservation is guarded by a compare-and-swap on ``risk_state.version``:
    if another process reserved risk in between, the snapshot is stale and
    the evaluation is rejected (the caller retries on the next tick).
    """
    state = risk_state_row(s, env)
    seen_version = state.version
    decision = RiskEngine(cfg).evaluate(proposal, pf, spec, rates)
    row = RiskDecisionRow(
        environment=env.value,
        bot_id=proposal.bot_id,
        symbol=proposal.symbol,
        signal_key=signal_key,
        cycle_id=cycle_id,
        approved=decision.approved,
        volume=decision.volume,
        planned_risk=decision.planned_risk,
        planned_risk_pct=decision.planned_risk_pct,
        risk_limit_pct=decision.risk_limit_pct,
        checks=[c.__dict__ for c in decision.checks],
        reasons=decision.reasons,
        legs=decision.legs,
    )
    if decision.approved and spec is not None:
        res = s.execute(
            update(RiskState)
            .where(RiskState.environment == env.value, RiskState.version == seen_version)
            .values(version=seen_version + 1, updated_at=utcnow())
        )
        if cast(CursorResult, res).rowcount != 1:
            decision.approved = False
            decision.volume = 0.0
            decision.checks.append(
                type(decision.checks[0])(
                    "risk_state_concurrency", False, "risk state changed concurrently; re-evaluate"
                )
            )
            row.approved = False
            row.reasons = decision.reasons
        else:
            s.add(
                RiskReservation(
                    environment=env.value,
                    cycle_id=cycle_id,
                    bot_id=proposal.bot_id,
                    bot_kind=proposal.bot_kind.value,
                    symbol=proposal.symbol,
                    base_currency=spec.base_currency,
                    quote_currency=spec.quote_currency,
                    correlation_group=spec.correlation_group,
                    planned_risk=decision.planned_risk,
                )
            )
    s.add(row)
    return decision


def release_reservation(s: Session, cycle_id: str) -> None:
    for r in s.scalars(
        select(RiskReservation).where(RiskReservation.cycle_id == cycle_id, RiskReservation.active.is_(True))
    ):
        r.active = False
        r.released_at = utcnow()


# --- status for the UI -------------------------------------------------------------
def risk_status(
    s: Session,
    env: Environment,
    account: AccountInfo | None,
    registry: InstrumentRegistry,
    quotes: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    cfg, _ = current_risk_config(s)
    eng = RiskEngine(cfg)
    state = s.get(RiskState, env.value)
    equity = account.equity if account else None
    day_start = (state.day_start_equity if state and state.day_start_equity else equity) or 0.0
    peak = (state.peak_equity if state and state.peak_equity else equity) or 0.0
    items = open_items(s, env)
    dl_limit = day_start * eng.daily_loss_limit_pct(env) / 100
    dl = daily_loss(day_start, equity) if equity is not None else 0.0
    exposure: dict[str, float] = {}
    for i in items:
        for c in (i.base_currency, i.quote_currency):
            exposure[c] = exposure.get(c, 0.0) + i.planned_risk
    breakers = active_breaker_rows(s, env)
    halts = [f"{b.type}: {b.reason}" for b in breakers if b.critical and b.scope == "global"]
    since = (state.day_start_at if state else None) or (now - timedelta(days=1))
    blocked = (
        s.scalar(
            select(func.count())
            .select_from(RiskDecisionRow)
            .where(
                RiskDecisionRow.approved.is_(False),
                RiskDecisionRow.created_at >= since,
                RiskDecisionRow.environment == env.value,
            )
        )
        or 0
    )
    instruments = []
    for spec in registry.all():
        q = quotes.get(spec.symbol)
        o = cfg.instruments.get(spec.symbol)
        instruments.append(
            {
                "symbol": spec.symbol,
                "spread_pips": round(spec.price_to_pips(q.spread), 2) if q else None,
                "max_spread_pips": cfg.max_spread_pips(spec.symbol),
                "enabled": spec.tradable and (o is None or o.enabled),
                "quote_age_seconds": round((now - q.time).total_seconds(), 1) if q else None,
            }
        )
    return {
        "currency": account.currency if account else None,
        "equity": equity,
        "day_start_equity": day_start,
        "daily_loss": round(dl, 2),
        "daily_loss_limit": round(dl_limit, 2),
        "daily_loss_used_pct": round(dl / dl_limit * 100, 2) if dl_limit else None,
        "drawdown_pct": round(drawdown_pct(peak, equity), 3) if equity is not None else None,
        "drawdown_limit_pct": cfg.global_.max_drawdown_pct,
        "open_risk": round(sum(i.planned_risk for i in items), 2),
        "open_risk_cap": round((equity or 0) * cfg.global_.max_total_open_risk_pct / 100, 2),
        "active_cycles": len(items),
        "max_concurrent_cycles": cfg.global_.max_concurrent_cycles,
        "currency_exposure": [
            {
                "currency": c,
                "planned_risk": round(v, 2),
                "cap": round((equity or 0) * cfg.global_.max_currency_risk_pct / 100, 2),
            }
            for c, v in sorted(exposure.items())
        ],
        "instruments": instruments,
        "halted": bool(halts),
        "halt_reasons": halts,
        "blocked_attempts_today": int(blocked),
        "hard_limits": HARD_LIMITS,
    }
