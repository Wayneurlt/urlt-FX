"""Trading orchestration: worker ticks, entries, management, liquidation.

Every function that touches broker or cycle state runs inside the
``trading:<environment>`` lease, so only one process mutates trading state at
a time. Within an operation a single DB session is shared by the execution
store and the simulated broker; both commit after each consequential step.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import APIError, bad_request, require_confirm
from app.db.base import session_factory, utcnow
from app.models import AccountSnapshot, Bot, EconomicEventRow, RiskState, TradingCycle, TradingSignal
from app.services import risk_service
from app.services.audit import audit, notify
from app.services.bots_service import current_config, get_bot
from app.services.broker_service import BrokerUnavailable, build_broker, calendar_provider, sim_state_row
from app.services.execution_store import DbExecutionStore, load_active_cycles, record_from_row
from app.services.instruments import load_registry
from app.services.leases import lease
from app.services.system_settings import current_environment, get_setting, get_time_setting, set_setting
from trading.adapters.base import (
    AccountInfo,
    BrokerAdapter,
    BrokerError,
    ConnectionStatus,
    OwnershipViolation,
)
from trading.engine.signals import evaluate_daily, evaluate_news_volatility
from trading.execution.dual_entry import DualEntryExecutor
from trading.execution.records import CycleRecord
from trading.execution.single_entry import SingleTradeExecutor
from trading.instruments.spec import InstrumentRegistry
from trading.interfaces.types import (
    BotKind,
    BotStatus,
    Environment,
    ExitReason,
    MarginMode,
    Side,
    TriggerType,
)
from trading.market_data.provider import MarketDataProvider
from trading.market_data.quality import check_quotes_freshness
from trading.news.provider import CalendarProvider, CalendarUnavailable
from trading.risk.circuit_breakers import BreakerScope, BreakerType
from trading.risk.config import RiskConfig
from trading.risk.engine import TradeProposal, evaluate_halts
from trading.strategies.base import StrategySignal, dual_leg_plans
from trading.strategies.daily_trading.config import DailyTradingConfig
from trading.strategies.daily_trading.trend_following import build_daily_strategy
from trading.strategies.news_volatility.config import NewsVolatilityConfig

log = logging.getLogger("trading.runtime")

EVALUATION_INTERVAL = timedelta(seconds=5)
SNAPSHOT_INTERVAL = timedelta(seconds=30)
CALENDAR_SYNC_INTERVAL = timedelta(minutes=5)


@dataclass
class Ctx:
    s: Session
    env: Environment
    registry: InstrumentRegistry
    invalid_specs: list[str]
    broker: BrokerAdapter
    market: MarketDataProvider
    risk: RiskConfig
    calendar: CalendarProvider | None
    actor: str = "system"

    def now(self) -> datetime:
        return utcnow()

    @property
    def store(self) -> DbExecutionStore:
        return DbExecutionStore(self.s, self.env, self.actor)


def open_ctx(s: Session, env: Environment, actor: str = "system") -> Ctx:
    registry, invalid = load_registry(s)
    broker, market = build_broker(s, env, registry, utcnow)
    risk, _ = risk_service.current_risk_config(s)
    return Ctx(s, env, registry, invalid, broker, market, risk, calendar_provider(s), actor)


def _lease_name(env: Environment) -> str:
    return f"trading:{env.value}"


# --- executors -----------------------------------------------------------------------
def executor_for(ctx: Ctx, rec: CycleRecord) -> DualEntryExecutor | SingleTradeExecutor:
    spec = ctx.registry.get(rec.symbol)
    if rec.kind == "dual_entry":
        cfg = (
            NewsVolatilityConfig.model_validate(rec.config_snapshot)
            if rec.config_snapshot
            else NewsVolatilityConfig()
        )
        return DualEntryExecutor(ctx.broker, ctx.store, spec, ctx.now, ctx.risk.global_, cfg)
    dcfg = (
        DailyTradingConfig.model_validate(rec.config_snapshot)
        if rec.config_snapshot
        else DailyTradingConfig()
    )
    return SingleTradeExecutor(ctx.broker, ctx.store, spec, ctx.now, ctx.risk.global_, dcfg)


def manage_active_cycles(ctx: Ctx) -> int:
    n = 0
    for row in load_active_cycles(ctx.s, ctx.env):
        rec = record_from_row(row)
        if rec.state in ("created", "validating"):
            # Interrupted before any order intent was written: nothing was sent.
            rec.state = "rejected"
            rec.error = "interrupted before submission; no orders were sent"
            ctx.store.save(rec)
            continue
        try:
            executor_for(ctx, rec).manage(rec)
            n += 1
        except OwnershipViolation as exc:
            risk_service.trip_breaker(
                ctx.s,
                ctx.env,
                BreakerType.RECONCILIATION_FAILURE,
                BreakerScope.GLOBAL,
                None,
                f"ownership violation: {exc}",
            )
            audit(
                ctx.s,
                "execution",
                f"ownership violation while managing cycle {rec.id}: {exc}",
                "critical",
                cycle_id=rec.id,
                bot_id=rec.bot_id,
            )
            ctx.s.commit()
    return n


# --- account / risk state ---------------------------------------------------------------
def record_snapshot(ctx: Ctx, account: AccountInfo, force: bool = False) -> None:
    last = ctx.s.scalar(
        select(func.max(AccountSnapshot.time)).where(AccountSnapshot.environment == ctx.env.value)
    )
    if not force and last is not None and ctx.now() - last < SNAPSHOT_INTERVAL:
        return
    ctx.s.add(
        AccountSnapshot(
            environment=ctx.env.value,
            currency=account.currency,
            balance=account.balance,
            equity=account.equity,
            margin=account.margin,
            free_margin=account.free_margin,
            margin_level=account.margin_level,
            floating_pnl=round(account.equity - account.balance, 2),
            simulated=account.is_simulated,
            source=ctx.broker.name,
            time=ctx.now(),
        )
    )


def implausible_account(ctx: Ctx, account: AccountInfo) -> str | None:
    """Reasons a broker account reading should not be trusted (None = looks sane)."""
    if account.is_simulated:
        return None
    last = ctx.s.scalars(
        select(AccountSnapshot)
        .where(AccountSnapshot.environment == ctx.env.value)
        .order_by(AccountSnapshot.time.desc())
        .limit(1)
    ).first()
    if account.equity <= 0 and account.balance <= 0 and (last is None or last.balance > 0):
        return "broker reported an empty account (balance and equity 0) - reading ignored until it recovers"
    if last is not None and last.equity > 0 and account.equity <= 0 < account.balance:
        return (
            f"broker reported equity 0 with balance {account.balance:.2f} - reading ignored until it recovers"
        )
    return None


def apply_halts(ctx: Ctx, account: AccountInfo) -> None:
    pf = risk_service.portfolio_snapshot(ctx.s, ctx.env, account, ctx.risk, ctx.now())
    for btype, reason in evaluate_halts(ctx.risk, pf, ctx.env):
        risk_service.trip_breaker(ctx.s, ctx.env, BreakerType(btype), BreakerScope.GLOBAL, None, reason)
    for bot_id, n in pf.consecutive_losses.items():
        if n >= ctx.risk.global_.max_consecutive_losses:
            risk_service.trip_breaker(
                ctx.s,
                ctx.env,
                BreakerType.CONSECUTIVE_LOSSES,
                BreakerScope.BOT,
                bot_id,
                f"{n} consecutive losing cycles/trades (limit {ctx.risk.global_.max_consecutive_losses})",
            )
    ctx.s.flush()
    # Move bots into RISK_HALTED when a critical breaker blocks them.
    breakers = risk_service.active_breakers(ctx.s, ctx.env)
    for bot in ctx.s.scalars(select(Bot)):
        blocking = [
            b
            for b in breakers
            if b.critical and b.applies_to(bot.id, "*") and b.scope is not BreakerScope.INSTRUMENT
        ]
        if blocking and bot.status in (BotStatus.RUNNING.value, BotStatus.PAUSED.value):
            bot.status = BotStatus.RISK_HALTED.value
            audit(
                ctx.s,
                "bot_lifecycle",
                f"{bot.name} risk-halted: {blocking[0].type.value}: {blocking[0].reason}",
                "critical",
                bot_id=bot.id,
                environment=ctx.env.value,
            )
    ctx.s.commit()
    if ctx.risk.global_.halt_policy == "close_all" and any(
        b.type in (BreakerType.DAILY_LOSS_LIMIT, BreakerType.MAX_DRAWDOWN) for b in breakers
    ):
        for row in load_active_cycles(ctx.s, ctx.env):
            rec = record_from_row(row)
            audit(
                ctx.s,
                "risk",
                f"halt policy close_all: liquidating cycle {rec.id}",
                "critical",
                cycle_id=rec.id,
                bot_id=rec.bot_id,
            )
            executor_for(ctx, rec).liquidate(rec, ExitReason.LIQUIDATION)


def check_market_health(ctx: Ctx, symbols: set[str]) -> dict[str, Any]:
    quotes = {}
    for sym in sorted(symbols):
        if not ctx.registry.has(sym):
            continue
        try:
            q = ctx.broker.get_quote(sym)
        except BrokerError as exc:
            risk_service.trip_breaker(
                ctx.s,
                ctx.env,
                BreakerType.STALE_MARKET_DATA,
                BreakerScope.INSTRUMENT,
                sym,
                f"no quote: {exc}",
            )
            continue
        quotes[sym] = q
        stale = check_quotes_freshness(q.time, ctx.now(), ctx.risk.global_.max_quote_age_seconds)
        if stale:
            risk_service.trip_breaker(
                ctx.s, ctx.env, BreakerType.STALE_MARKET_DATA, BreakerScope.INSTRUMENT, sym, stale
            )
        else:
            risk_service.auto_clear(
                ctx.s, ctx.env, BreakerType.STALE_MARKET_DATA, sym, "fresh quotes received"
            )
        spec = ctx.registry.get(sym)
        sp, lim = spec.price_to_pips(q.spread), ctx.risk.max_spread_pips(sym)
        if sp > lim and ctx.market.market_status(sym, ctx.now()).open:
            risk_service.trip_breaker(
                ctx.s,
                ctx.env,
                BreakerType.EXCESSIVE_SPREAD,
                BreakerScope.INSTRUMENT,
                sym,
                f"spread {sp:.1f} pips > {lim}",
            )
        else:
            risk_service.auto_clear(
                ctx.s, ctx.env, BreakerType.EXCESSIVE_SPREAD, sym, "spread back within limit"
            )
    if quotes:
        set_setting(ctx.s, "last_market_update", max(q.time for q in quotes.values()).isoformat())
    risk_service.notify_lingering_blips(ctx.s, ctx.env, ctx.now())
    return quotes


def _probe_market_freshness(ctx: Ctx, bots: list[Bot]) -> None:
    """With no bot running, still record that the price feed is alive (no breakers, no notifications)."""
    configured = sorted(
        {sym for b in bots for sym in current_config(ctx.s, b).instruments if ctx.registry.has(sym)}
    )
    for sym in configured[:1]:
        try:
            q = ctx.broker.get_quote(sym)
        except BrokerError:
            return
        set_setting(ctx.s, "last_market_update", q.time.isoformat())


# --- bot pre-start checks -------------------------------------------------------------------
def prestart_checks(ctx: Ctx, bot: Bot) -> list[str]:
    problems: list[str] = []
    try:
        conn = ctx.broker.check_connection()
        if not conn.connected:
            problems.append(f"broker not connected: {conn.detail}")
        if not ctx.broker.trading_allowed():
            problems.append(
                "Algo Trading is switched off in the MT5 terminal (toolbar button) - orders would be refused"
            )
        acct = ctx.broker.account_info()
        if bot.kind == BotKind.NEWS_VOLATILITY.value and acct.margin_mode is not MarginMode.HEDGING:
            problems.append(
                f"account margin mode is {acct.margin_mode.value}: the dual BUY+SELL strategy needs a hedging account and is disabled"
            )
    except BrokerError as exc:
        problems.append(f"broker check failed: {exc}")
    try:
        cfg = current_config(ctx.s, bot)
        valid = [s for s in cfg.instruments if ctx.registry.has(s)]
        if not valid:
            problems.append("no configured instrument has a validated specification")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"invalid configuration: {exc}")
    if ctx.env is Environment.LIVE and not get_setting(ctx.s, "live_trading_enabled"):
        problems.append("live trading is not enabled")
    return problems


def process_starting_bots(ctx: Ctx) -> None:
    for bot in ctx.s.scalars(select(Bot).where(Bot.status == BotStatus.STARTING.value)):
        problems = prestart_checks(ctx, bot)
        if problems:
            bot.status = BotStatus.ERROR.value
            bot.last_error = "; ".join(problems)
            audit(
                ctx.s,
                "bot_lifecycle",
                f"{bot.name} failed pre-start checks: {bot.last_error}",
                "error",
                bot_id=bot.id,
                environment=ctx.env.value,
            )
            notify(ctx.s, "bot_error", f"{bot.name}: {bot.last_error}", "error", bot_id=bot.id)
        else:
            bot.status = BotStatus.RUNNING.value
            audit(
                ctx.s,
                "bot_lifecycle",
                f"{bot.name} passed pre-start checks and is running in {ctx.env.value}",
                bot_id=bot.id,
                environment=ctx.env.value,
            )
    ctx.s.commit()


# --- entries ------------------------------------------------------------------------------------
def _processed_keys(s: Session, bot_id: str, since: datetime) -> set[str]:
    return set(
        s.scalars(
            select(TradingSignal.dedupe_key).where(
                TradingSignal.bot_id == bot_id, TradingSignal.created_at >= since
            )
        )
    )


def evaluate_bot(ctx: Ctx, bot: Bot, execute: bool = True, manual: bool = False) -> dict[str, Any]:
    cfg = current_config(ctx.s, bot)
    now = ctx.now()
    keys = _processed_keys(ctx.s, bot.id, now - timedelta(days=2))
    if isinstance(cfg, NewsVolatilityConfig):
        last = {
            k: datetime.fromisoformat(v)
            for k, v in (bot.runtime or {}).get("last_volatility_trigger", {}).items()
        }
        res = evaluate_news_volatility(bot.id, cfg, ctx.registry, ctx.market, ctx.calendar, now, keys, last)
        if cfg.entry_mode.news and any("calendar" in b for b in res.blocked):
            reason = next(b for b in res.blocked if "calendar" in b)
            if ctx.calendar is not None:
                risk_service.trip_breaker(
                    ctx.s, ctx.env, BreakerType.CALENDAR_PROVIDER_FAILURE, BreakerScope.BOT, bot.id, reason
                )
        elif cfg.entry_mode.news:
            risk_service.auto_clear(
                ctx.s, ctx.env, BreakerType.CALENDAR_PROVIDER_FAILURE, bot.id, "calendar fresh"
            )
    else:
        res = evaluate_daily(bot.id, cfg, ctx.registry, ctx.market, now, keys)
    bot.last_evaluation_at = now
    summary = {
        "time": now.isoformat(),
        "signals": [sg.dedupe_key for sg in res.signals],
        "blocked": res.blocked,
        "diagnostics": res.diagnostics[:20],
    }
    digest = hashlib.sha1(
        json.dumps(
            {
                "b": res.blocked,
                "d": [(d.get("symbol"), d.get("result"), d.get("reason")) for d in res.diagnostics],
                "s": summary["signals"],
            },
            default=str,
        ).encode()
    ).hexdigest()
    prev = (bot.runtime or {}).get("eval_digest")
    prev_at = (bot.runtime or {}).get("eval_logged_at")
    if digest != prev or not prev_at or now - datetime.fromisoformat(prev_at) > timedelta(minutes=5):
        audit(
            ctx.s,
            "strategy_evaluation",
            f"{bot.name}: {len(res.signals)} signal(s); " + ("; ".join(res.blocked) or "no blocks"),
            "debug" if not res.signals else "info",
            bot_id=bot.id,
            environment=ctx.env.value,
            data=summary,
        )
        bot.runtime = {**(bot.runtime or {}), "eval_digest": digest, "eval_logged_at": now.isoformat()}
    bot.last_evaluation = summary
    ctx.s.commit()
    entries = []
    if execute:
        for sig in res.signals:
            if manual:
                sig.details["manual"] = True
            entries.append(attempt_entry(ctx, bot, sig, cfg))
    return {
        "signals": [
            {
                "symbol": sg.symbol,
                "trigger": sg.trigger.value,
                "dedupe_key": sg.dedupe_key,
                "details": sg.details,
            }
            for sg in res.signals
        ],
        "diagnostics": res.diagnostics,
        "blocked": res.blocked,
        "entries": entries,
    }


def attempt_entry(
    ctx: Ctx, bot: Bot, sig: StrategySignal, cfg: NewsVolatilityConfig | DailyTradingConfig
) -> dict[str, Any]:
    s = ctx.s
    now = ctx.now()
    cycle_id = str(uuid.uuid4())
    # 1. Durable dedupe: one signal key -> at most one cycle.
    try:
        with s.begin_nested():
            s.add(
                TradingSignal(
                    bot_id=bot.id,
                    dedupe_key=sig.dedupe_key,
                    symbol=sig.symbol,
                    trigger_type=sig.trigger.value,
                    event_id=sig.event_id,
                    candle_time=sig.candle_time,
                    details=sig.details,
                    environment=ctx.env.value,
                    cycle_id=cycle_id,
                )
            )
            s.flush()
    except IntegrityError:
        return {
            "cycle_id": None,
            "approved": False,
            "reasons": [f"duplicate signal {sig.dedupe_key} already processed"],
        }
    if sig.trigger is TriggerType.VOLATILITY and sig.candle_time:
        rt = dict(bot.runtime or {})
        rt["last_volatility_trigger"] = {**rt.get("last_volatility_trigger", {}), sig.symbol: now.isoformat()}
        bot.runtime = rt
    s.commit()

    dual = bot.kind == BotKind.NEWS_VOLATILITY.value
    rec = CycleRecord(
        id=cycle_id,
        kind="dual_entry" if dual else "single",
        bot_id=bot.id,
        magic=bot.magic,
        strategy_version=bot.strategy_version,
        trigger_type=sig.trigger.value,
        symbol=sig.symbol,
        environment=ctx.env.value,
        state="created",
        created_at=now,
        updated_at=now,
        signal_key=sig.dedupe_key,
        event_id=sig.event_id,
        signal={**sig.details, "trigger": sig.trigger.value},
        config_snapshot=cfg.model_dump(mode="json"),
        simulated=ctx.broker.is_simulated,
    )
    store = ctx.store
    store.save(rec)
    store.event(
        rec,
        "cycle_created",
        f"{sig.trigger.value} signal on {sig.symbol}" + (f" (event {sig.event_id})" if sig.event_id else ""),
    )
    rec.state = "validating"
    store.save(rec)

    def reject(reasons: list[str], decision: dict | None = None) -> dict[str, Any]:
        rec.state = "rejected"
        rec.error = "; ".join(reasons)[:2000]
        rec.risk_decision = decision or {"approved": False, "reasons": reasons}
        store.save(rec)
        sg = s.scalars(select(TradingSignal).where(TradingSignal.dedupe_key == sig.dedupe_key)).first()
        if sg:
            sg.status = "rejected"
        audit(
            s,
            "risk",
            f"{bot.name} entry on {sig.symbol} rejected: {rec.error}",
            "warning",
            bot_id=bot.id,
            cycle_id=rec.id,
            environment=ctx.env.value,
        )
        notify(
            s,
            "entry_rejected",
            f"{bot.name} {sig.symbol}: {reasons[0] if reasons else 'rejected'}",
            "warning",
            bot_id=bot.id,
            dedupe_window=timedelta(minutes=10),
        )
        s.commit()
        return {"cycle_id": rec.id, "approved": False, "reasons": reasons}

    # 2. Eligibility re-checks at execution time (state may have changed since the signal).
    s.refresh(bot)
    if bot.status not in (BotStatus.RUNNING.value,) and not sig.details.get("manual"):
        return reject([f"bot is {bot.status}; new entries are not permitted"])
    if not ctx.registry.has(sig.symbol):
        return reject([f"{sig.symbol} has no validated instrument specification"])
    spec = ctx.registry.get(sig.symbol)
    status = ctx.market.market_status(sig.symbol, now)
    if not status.open:
        return reject([f"market closed: {status.reason}"])
    try:
        quote = ctx.broker.get_quote(sig.symbol)
        account = ctx.broker.account_info()
        caps = ctx.broker.capabilities()
    except BrokerError as exc:
        return reject([f"broker state cannot be verified: {exc}"])
    if dual and not caps.hedging:
        return reject(
            [
                "account does not support independent opposing positions (netting); dual-entry strategy disabled"
            ]
        )

    if dual:
        assert isinstance(cfg, NewsVolatilityConfig)
        slip = cfg.est_slippage_points
    else:
        assert isinstance(cfg, DailyTradingConfig)
        today = (
            s.scalar(
                select(func.count())
                .select_from(TradingCycle)
                .where(
                    TradingCycle.bot_id == bot.id,
                    TradingCycle.environment == ctx.env.value,
                    TradingCycle.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
                    TradingCycle.state.notin_(("rejected", "failed")),
                    TradingCycle.id != rec.id,
                )
            )
            or 0
        )
        if today >= cfg.max_trades_per_day:
            return reject([f"max trades per day reached ({today}/{cfg.max_trades_per_day})"])
        last = s.scalar(
            select(func.max(TradingCycle.created_at)).where(
                TradingCycle.bot_id == bot.id,
                TradingCycle.symbol == sig.symbol,
                TradingCycle.environment == ctx.env.value,
                TradingCycle.state.notin_(("rejected", "failed")),
                TradingCycle.id != rec.id,
            )
        )
        if last and now - last < timedelta(minutes=cfg.cooldown_minutes):
            return reject(
                [
                    f"cooldown active on {sig.symbol} until {(last + timedelta(minutes=cfg.cooldown_minutes)).isoformat()}"
                ]
            )
        slip = cfg.est_slippage_points
        rec.management = {"atr": sig.details.get("atr")}

    margin_est = None
    try:
        margin_est = ctx.broker.estimate_margin(sig.symbol, Side.BUY, 1.0, quote.mid)
    except BrokerError:
        margin_est = None
    pf = risk_service.portfolio_snapshot(s, ctx.env, account, ctx.risk, now)
    rates = ctx.market.rates(now)

    # Fetch the price LAST and judge it against the moment it arrived: the database work above
    # can take seconds on a remote DB, which would make an earlier price look stale (or "future").
    try:
        quote = ctx.broker.get_quote(sig.symbol)
    except BrokerError as exc:
        return reject([f"broker state cannot be verified: {exc}"])
    priced_at = ctx.now()
    tp = None
    if dual:
        assert isinstance(cfg, NewsVolatilityConfig)
        plans = dual_leg_plans(quote, spec, float(sig.details.get("stop_pips") or cfg.stop_pips(sig.symbol)))
    else:
        assert isinstance(cfg, DailyTradingConfig)
        strat = build_daily_strategy(cfg.strategy_name, cfg.params, cfg.timeframe, bot.id)
        plan, tp = strat.leg_plan(sig, quote, spec)
        plans = [plan]
    proposal = TradeProposal(
        bot_id=bot.id,
        bot_kind=BotKind(bot.kind),
        environment=ctx.env,
        symbol=sig.symbol,
        legs=plans,
        quote=quote,
        now=priced_at,
        commission_per_lot_side=getattr(getattr(ctx.broker, "config", None), "commission_per_lot_side", 0.0),
        est_slippage_points=slip,
        margin_estimate_per_lot=margin_est,
    )
    decision = risk_service.evaluate_and_reserve(
        s, ctx.env, ctx.risk, proposal, pf, spec, rates, rec.id, sig.dedupe_key
    )
    s.commit()
    rec.risk_decision = decision.as_dict()
    rec.planned_risk = decision.planned_risk
    if not decision.approved:
        return reject(decision.reasons, decision.as_dict())
    store.save(rec)

    # 3. Execute.
    try:
        if dual:
            ex = executor_for(ctx, rec)
            assert isinstance(ex, DualEntryExecutor)
            ex.open(rec, plans, decision.volume)
        else:
            ex2 = executor_for(ctx, rec)
            assert isinstance(ex2, SingleTradeExecutor)
            ex2.open(rec, plans[0], decision.volume, tp)
    except BrokerError as exc:
        audit(s, "execution", f"entry failed for {rec.id}: {exc}", "error", bot_id=bot.id, cycle_id=rec.id)
        s.commit()
    bot.last_execution_at = ctx.now()
    sg = s.scalars(select(TradingSignal).where(TradingSignal.dedupe_key == sig.dedupe_key)).first()
    if sg:
        sg.status = "accepted"
    s.commit()
    return {"cycle_id": rec.id, "approved": True, "reasons": [], "state": rec.state}


# --- worker tick ------------------------------------------------------------------------------------
def run_tick(env: Environment | None = None) -> dict[str, Any]:
    st = get_settings()
    s = session_factory()()
    try:
        env = env or current_environment(s)
        with lease(_lease_name(env), st.trading_lock_lease_seconds, wait_seconds=1.0):
            return _tick(s, env)
    finally:
        s.close()


def _tick(s: Session, env: Environment) -> dict[str, Any]:
    out: dict[str, Any] = {"environment": env.value}
    try:
        ctx = open_ctx(s, env)
    except BrokerUnavailable as exc:
        risk_service.trip_breaker(
            s, env, BreakerType.BROKER_DISCONNECTED, BreakerScope.GLOBAL, None, exc.message
        )
        s.commit()
        return {**out, "error": exc.message}
    for msg in ctx.invalid_specs:
        risk_service.trip_breaker(s, env, BreakerType.INVALID_INSTRUMENT_SPEC, BreakerScope.GLOBAL, None, msg)

    conn = ctx.broker.check_connection()
    if not conn.connected:
        # Try to reconnect first; only a failed reconnect is a real disconnection.
        try:
            conn = ctx.broker.connect()
        except BrokerError as exc:
            conn = ConnectionStatus(False, str(exc))
    if not conn.connected:
        risk_service.trip_breaker(
            s, env, BreakerType.BROKER_DISCONNECTED, BreakerScope.GLOBAL, None, conn.detail or "disconnected"
        )
        s.commit()
        return {**out, "error": "broker disconnected"}
    trading_ok = ctx.broker.trading_allowed()
    if not trading_ok:
        risk_service.trip_breaker(
            s,
            env,
            BreakerType.BROKER_DISCONNECTED,
            BreakerScope.GLOBAL,
            None,
            "Algo Trading is switched off in the MT5 terminal - every order would be refused; turn it on (toolbar button)",
        )
        s.commit()
    if hasattr(ctx.broker, "process_market"):
        ctx.broker.process_market()

    # Existing exposure is always managed, whatever the bots' statuses.
    out["managed_cycles"] = manage_active_cycles(ctx)
    set_setting(s, "last_reconciliation", ctx.now().isoformat())

    try:
        account = ctx.broker.account_info()
        problem = implausible_account(ctx, account)
    except BrokerError as exc:
        problem = f"account data unavailable: {exc}"
    if problem:
        # Never record or act on account numbers we can't trust (e.g. equity 0 mid-login):
        # that would trip false loss halts. Block new entries until the data is sane again.
        risk_service.trip_breaker(s, env, BreakerType.BROKER_DISCONNECTED, BreakerScope.GLOBAL, None, problem)
        s.commit()
        return {**out, "error": problem}
    if trading_ok:  # connected, accepting orders and the account reading is sane
        risk_service.auto_clear(
            s, env, BreakerType.BROKER_DISCONNECTED, None, "broker connected and reconciled"
        )
    record_snapshot(ctx, account)
    apply_halts(ctx, account)

    symbols: set[str] = set()
    bots = list(s.scalars(select(Bot)))
    for b in bots:
        if b.status in (BotStatus.RUNNING.value, BotStatus.PAUSED.value, BotStatus.STARTING.value):
            symbols.update(current_config(s, b).instruments)
    symbols.update(r.symbol for r in load_active_cycles(s, env))
    quotes = check_market_health(ctx, symbols)
    if not quotes:
        _probe_market_freshness(ctx, bots)
    _sync_calendar(ctx)
    process_starting_bots(ctx)
    s.commit()

    evaluated = []
    for b in bots:
        s.refresh(b)
        if b.status != BotStatus.RUNNING.value:
            continue
        if b.last_evaluation_at and ctx.now() - b.last_evaluation_at < EVALUATION_INTERVAL:
            continue
        try:
            r = evaluate_bot(ctx, b)
            evaluated.append({"bot": b.id, "signals": len(r["signals"]), "entries": r["entries"]})
        except Exception as exc:  # noqa: BLE001 - a strategy failure must not stop position management
            s.rollback()
            b = s.get(Bot, b.id)  # type: ignore[assignment]
            b.status = BotStatus.ERROR.value
            b.last_error = f"evaluation failed: {exc}"
            audit(s, "bot_lifecycle", f"{b.name} evaluation failed: {exc}", "error", bot_id=b.id)
            notify(
                s,
                "bot_error",
                f"{b.name}: evaluation failed: {exc}",
                "error",
                bot_id=b.id,
                dedupe_window=timedelta(minutes=10),
            )
            s.commit()
            log.exception("bot evaluation failed")
    out["evaluated"] = evaluated
    s.commit()
    return out


def _sync_calendar(ctx: Ctx) -> None:
    if ctx.calendar is None:
        return
    last = get_time_setting(ctx.s, "last_calendar_sync")
    if last and ctx.now() - last < CALENDAR_SYNC_INTERVAL:
        return
    try:
        events = ctx.calendar.events(ctx.now() - timedelta(days=7), ctx.now() + timedelta(days=14))
    except CalendarUnavailable as exc:
        audit(ctx.s, "integration", f"calendar sync failed: {exc}", "warning")
        set_setting(ctx.s, "last_calendar_sync", ctx.now().isoformat())
        return
    for ev in events:
        row = ctx.s.scalars(
            select(EconomicEventRow).where(
                EconomicEventRow.provider == ev.provider, EconomicEventRow.event_id == ev.event_id
            )
        ).first()
        if row is None:
            row = EconomicEventRow(event_id=ev.event_id, provider=ev.provider)
            ctx.s.add(row)
        row.origin, row.currency, row.name, row.scheduled_at = (
            ev.origin.value,
            ev.currency,
            ev.name,
            ev.scheduled_at,
        )
        row.impact, row.status, row.forecast, row.previous, row.actual = (
            ev.impact.value,
            ev.status.value,
            ev.forecast,
            ev.previous,
            ev.actual,
        )
        row.fetched_at = ev.fetched_at
    set_setting(ctx.s, "last_calendar_sync", ctx.now().isoformat())


# --- user-triggered operations ----------------------------------------------------------------------
def _require_paper(env: Environment) -> None:
    if env is Environment.LIVE:
        raise APIError(
            409, "not_allowed_in_live", "this operation is only available in simulation or paper trading"
        )


def evaluate_now(bot_id: str, actor: str) -> dict[str, Any]:
    s = session_factory()()
    try:
        env = current_environment(s)
        _require_paper(env)
        with lease(_lease_name(env)):
            ctx = open_ctx(s, env, actor)
            bot = get_bot(s, bot_id)
            if bot.status not in (BotStatus.RUNNING.value, BotStatus.STOPPED.value):
                raise APIError(
                    409,
                    "invalid_state",
                    f"bot is {bot.status}; manual evaluation needs a stopped or running bot",
                )
            if hasattr(ctx.broker, "process_market"):
                ctx.broker.process_market()
            manage_active_cycles(ctx)
            # Manual mode: entries are allowed for a STOPPED bot because the user explicitly asked.
            res = evaluate_bot(ctx, bot, execute=True, manual=True)
            audit(
                s,
                "bot_lifecycle",
                f"manual evaluation of {bot.name}: {len(res['signals'])} signal(s)",
                actor=actor,
                bot_id=bot.id,
            )
            s.commit()
            return res
    finally:
        s.close()


def manual_entry(
    bot_id: str, symbol: str, side: str | None, stop_pips: float, target_pips: float | None, actor: str
) -> dict[str, Any]:
    s = session_factory()()
    try:
        env = current_environment(s)
        with lease(_lease_name(env)):
            ctx = open_ctx(s, env, actor)
            if env is Environment.LIVE:
                # Manual test entries in live mode are allowed only on a broker DEMO account.
                try:
                    demo = ctx.broker.account_info().is_demo
                except BrokerError as exc:
                    raise APIError(
                        409, "broker_unavailable", f"cannot verify the broker account: {exc}"
                    ) from exc
                if not demo:
                    raise APIError(
                        409,
                        "not_allowed_on_real_account",
                        "manual test entries are only allowed on a DEMO broker account, never with real money",
                    )
                audit(
                    s,
                    "execution",
                    f"manual DEMO test entry requested by {actor} on {symbol}",
                    "warning",
                    actor=actor,
                    bot_id=bot_id,
                )
                s.commit()
            bot = get_bot(s, bot_id)
            if bot.status in (
                BotStatus.DISABLED.value,
                BotStatus.PAUSED.value,
                BotStatus.RISK_HALTED.value,
                BotStatus.ERROR.value,
            ):
                raise APIError(409, "invalid_state", f"bot is {bot.status}; manual entries are not permitted")
            if not ctx.registry.has(symbol):
                raise bad_request(f"unknown instrument {symbol}")
            if stop_pips <= 0:
                raise bad_request("stop_pips must be positive")
            spec = ctx.registry.get(symbol)
            cfg = current_config(s, bot)
            key = f"manual:{bot.id}:{symbol}:{uuid.uuid4()}"
            details: dict[str, Any] = {"manual": True, "requested_by": actor, "stop_pips": stop_pips}
            if bot.kind == BotKind.NEWS_VOLATILITY.value:
                if side:
                    raise bad_request(
                        "the News & Volatility bot only opens dual BUY+SELL cycles; omit 'side'"
                    )
                sig = StrategySignal(
                    symbol=symbol,
                    trigger=TriggerType.MANUAL,
                    dedupe_key=key,
                    candle_time=None,
                    details=details,
                )
            else:
                if side not in ("BUY", "SELL"):
                    raise bad_request("the Daily bot needs side BUY or SELL")
                sig = StrategySignal(
                    symbol=symbol,
                    trigger=TriggerType.MANUAL,
                    dedupe_key=key,
                    candle_time=None,
                    side=Side(side),
                    stop_distance=spec.pips_to_price(stop_pips),
                    target_distance=spec.pips_to_price(target_pips) if target_pips else None,
                    details=details,
                )
            return attempt_entry(ctx, bot, sig, cfg)
    finally:
        s.close()


def liquidate_cycle(cycle_id: str, actor: str, confirm_text: str | None) -> dict[str, Any]:
    require_confirm(confirm_text, "LIQUIDATE", "closing this cycle will close its open positions at market")
    s = session_factory()()
    try:
        row = s.get(TradingCycle, cycle_id)
        if row is None:
            raise APIError(404, "not_found", "cycle not found")
        env = Environment(row.environment)
        with lease(_lease_name(env)):
            ctx = open_ctx(s, env, actor)
            rec = record_from_row(row)
            if not row.active:
                raise APIError(409, "not_active", "cycle is not active")
            audit(
                s,
                "execution",
                f"liquidation of cycle {rec.id} authorised by {actor}",
                "warning",
                cycle_id=rec.id,
                bot_id=rec.bot_id,
                actor=actor,
            )
            notify(
                s,
                "liquidation",
                f"{rec.symbol} cycle {rec.id[:8]} liquidation requested by {actor}",
                "warning",
                bot_id=rec.bot_id,
                cycle_id=rec.id,
            )
            s.commit()
            executor_for(ctx, rec).liquidate(rec, ExitReason.LIQUIDATION)
            return {"cycle_id": rec.id, "state": rec.state, "realized_pnl": rec.realized_pnl}
    finally:
        s.close()


def shutdown_preview(s: Session, bot_id: str) -> dict[str, Any]:
    env = current_environment(s)
    bot = get_bot(s, bot_id)
    cycles = load_active_cycles(s, env, bot_id)
    risk, _ = risk_service.current_risk_config(s)
    return {
        "bot_id": bot.id,
        "cycles": cycles,
        "policy": {
            "stop": "No new entries; existing positions keep being managed until their stops/exits.",
            "liquidate": "All open positions of this bot are closed at market (requires typing LIQUIDATE). Forced exits can realise losses.",
            "halt_policy": risk.global_.halt_policy,
        },
    }


def shutdown_bot(bot_id: str, liquidate: bool, actor: str, confirm_text: str | None) -> dict[str, Any]:
    if liquidate:
        require_confirm(
            confirm_text,
            "LIQUIDATE",
            "orderly shutdown with liquidation closes all of this bot's positions at market",
        )
    s = session_factory()()
    try:
        env = current_environment(s)
        with lease(_lease_name(env)):
            bot = get_bot(s, bot_id)
            if bot.status not in (BotStatus.STOPPED.value, BotStatus.DISABLED.value):
                from app.services.bots_service import apply_action

                apply_action(s, bot, "stop", actor, env, None)
                s.commit()
            closed = []
            if liquidate:
                ctx = open_ctx(s, env, actor)
                for row in load_active_cycles(s, env, bot_id):
                    rec = record_from_row(row)
                    audit(
                        s,
                        "execution",
                        f"shutdown liquidation of cycle {rec.id} by {actor}",
                        "warning",
                        cycle_id=rec.id,
                        bot_id=bot_id,
                        actor=actor,
                    )
                    s.commit()
                    executor_for(ctx, rec).liquidate(rec, ExitReason.LIQUIDATION)
                    closed.append(rec.id)
            audit(
                s,
                "bot_lifecycle",
                f"{bot.name} orderly shutdown (liquidate={liquidate}, closed={len(closed)})",
                "warning",
                bot_id=bot_id,
                actor=actor,
            )
            s.commit()
            return {"bot_id": bot_id, "closed_cycles": closed}
    finally:
        s.close()


def reset_paper_account(initial_balance: float, actor: str, confirm_text: str | None) -> dict[str, Any]:
    require_confirm(
        confirm_text,
        "RESET PAPER ACCOUNT",
        "this resets the simulated balance and closes nothing; audit logs and history are kept",
    )
    if not 100 <= initial_balance <= 10_000_000:
        raise bad_request("initial_balance must be between 100 and 10,000,000")
    s = session_factory()()
    try:
        env = current_environment(s)
        _require_paper(env)
        with lease(_lease_name(env)):
            if load_active_cycles(s, env):
                raise APIError(
                    409,
                    "active_cycles",
                    "close or finish all active cycles before resetting the paper account",
                )
            running = [
                b.name
                for b in s.scalars(select(Bot).where(Bot.status.in_(("running", "starting", "paused"))))
            ]
            if running:
                raise APIError(409, "bots_running", f"stop all bots before resetting: {', '.join(running)}")
            row = sim_state_row(s, env)
            row.settings = {**row.settings, "initial_balance": initial_balance}
            row.reset_at = utcnow()
            s.flush()
            ctx = open_ctx(s, env, actor)
            ctx.broker.reset(initial_balance)  # type: ignore[attr-defined]  # keeps simulator ids unique
            rs = s.get(RiskState, env.value)
            if rs:
                rs.day_start_at = None
                rs.day_start_equity = None
                rs.peak_equity = None
                rs.version += 1
            audit(
                s,
                "configuration",
                f"{env.value} account reset to {initial_balance:.2f} by {actor} (history and audit logs kept)",
                "warning",
                actor=actor,
            )
            s.commit()
            return {"environment": env.value, "initial_balance": initial_balance, "reset_at": row.reset_at}
    finally:
        s.close()
