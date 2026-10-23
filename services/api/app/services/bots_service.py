from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError, bad_request, not_found, require_confirm
from app.db.base import utcnow
from app.models import Bot, BotConfig, PositionLeg, RiskState, StrategyVersion, TradingCycle
from app.services.audit import audit, notify
from app.services.risk_service import active_breakers, current_risk_config
from trading.engine.lifecycle import InvalidBotTransition, allowed_actions, next_status
from trading.instruments.spec import InstrumentRegistry
from trading.interfaces.types import BotKind, BotStatus, Environment
from trading.risk.engine import RiskEngine
from trading.strategies.daily_trading.config import DailyTradingConfig
from trading.strategies.daily_trading.trend_following import VERSION as DAILY_VERSION
from trading.strategies.news_volatility.config import STRATEGY_NAME as NV_NAME
from trading.strategies.news_volatility.config import STRATEGY_VERSION as NV_VERSION
from trading.strategies.news_volatility.config import NewsVolatilityConfig

BOT_DEFS = [
    {
        "id": "news-volatility",
        "name": "News & Volatility Bot",
        "kind": BotKind.NEWS_VOLATILITY.value,
        "magic": 1001,
        "strategy_name": NV_NAME,
        "strategy_version": NV_VERSION,
        "description": "Dual BUY+SELL entry around eligible news events and volatility breakouts; fixed-distance trailing stop on the surviving leg after the other leg is confirmed stopped out.",
    },
    {
        "id": "daily-trading",
        "name": "Daily Trading Bot",
        "kind": BotKind.DAILY_TRADING.value,
        "magic": 2001,
        "strategy_name": "trend_following_baseline",
        "strategy_version": DAILY_VERSION,
        "description": "EXPERIMENTAL intraday trend-following baseline: EMA trend filter + RSI momentum confirmation, ATR stop/target. Not a proven profitable system.",
    },
]


def config_model(kind: str) -> type[NewsVolatilityConfig] | type[DailyTradingConfig]:
    return NewsVolatilityConfig if kind == BotKind.NEWS_VOLATILITY.value else DailyTradingConfig


def seed_bots(s: Session) -> None:
    for d in BOT_DEFS:
        if s.get(Bot, d["id"]) is None:
            s.add(
                Bot(
                    id=d["id"],
                    name=d["name"],
                    kind=d["kind"],
                    magic=d["magic"],
                    strategy_name=d["strategy_name"],
                    strategy_version=d["strategy_version"],
                    status=BotStatus.DISABLED.value,
                    enabled=False,
                )
            )
            s.flush()
            s.add(
                BotConfig(
                    bot_id=d["id"],
                    version=1,
                    config=config_model(str(d["kind"]))().model_dump(mode="json"),
                    created_by="system",
                    note="default configuration",
                )
            )
        if (
            s.scalars(
                select(StrategyVersion).where(
                    StrategyVersion.name == d["strategy_name"],
                    StrategyVersion.version == d["strategy_version"],
                )
            ).first()
            is None
        ):
            s.add(
                StrategyVersion(
                    kind=d["kind"],
                    name=d["strategy_name"],
                    version=d["strategy_version"],
                    description=d["description"],
                )
            )


def get_bot(s: Session, bot_id: str) -> Bot:
    b = s.get(Bot, bot_id)
    if b is None:
        raise not_found("bot")
    return b


def current_config_row(s: Session, bot_id: str) -> BotConfig:
    row = s.scalars(
        select(BotConfig).where(BotConfig.bot_id == bot_id).order_by(BotConfig.version.desc()).limit(1)
    ).first()
    if row is None:
        raise not_found("bot configuration")
    return row


def current_config(s: Session, bot: Bot) -> NewsVolatilityConfig | DailyTradingConfig:
    return config_model(bot.kind).model_validate(current_config_row(s, bot.id).config)


def effective_settings(
    s: Session, bot: Bot, registry: InstrumentRegistry, env: Environment
) -> dict[str, Any]:
    """The settings actually applied after instrument validation and risk caps."""
    cfg = current_config(s, bot)
    risk, _ = current_risk_config(s)
    eng = RiskEngine(risk)
    kind = BotKind(bot.kind)
    included, excluded = [], []
    for sym in cfg.instruments:
        if not registry.has(sym):
            excluded.append({"symbol": sym, "reason": "no validated instrument specification"})
            continue
        spec = registry.get(sym)
        o = risk.instruments.get(sym)
        if not spec.tradable or (o is not None and not o.enabled):
            excluded.append({"symbol": sym, "reason": "disabled by instrument/risk settings"})
            continue
        item: dict[str, Any] = {"symbol": sym, "max_spread_pips": risk.max_spread_pips(sym)}
        if isinstance(cfg, NewsVolatilityConfig):
            item["initial_stop_pips"] = cfg.stop_pips(sym)
            item["trailing_distance_pips"] = cfg.trailing_pips(sym)
            item["min_stop_distance_pips"] = spec.price_to_pips(spec.min_stop_distance)
        included.append(item)
    risk_eff = {
        "entry_risk_pct": eng.entry_risk_pct(kind, env),
        "daily_loss_limit_pct": eng.daily_loss_limit_pct(env),
        "max_concurrent_cycles_global": risk.global_.max_concurrent_cycles,
        "max_quote_age_seconds": risk.global_.max_quote_age_seconds,
        "max_consecutive_losses": risk.global_.max_consecutive_losses,
    }
    if kind is BotKind.NEWS_VOLATILITY:
        risk_eff["max_cycles_per_instrument"] = risk.news_volatility.max_cycles_per_instrument
        risk_eff["max_concurrent_cycles_bot"] = risk.news_volatility.max_concurrent_cycles
        risk_eff["bot_daily_loss_pct"] = risk.news_volatility.max_daily_loss_pct
    else:
        risk_eff["max_concurrent_trades_bot"] = risk.daily_trading.max_concurrent_trades
        risk_eff["bot_daily_loss_pct"] = risk.daily_trading.max_daily_loss_pct
    return {
        "instruments": included,
        "excluded_instruments": excluded,
        "risk": risk_eff,
        "environment": env.value,
        "martingale": "disabled (not supported by design)",
        "loss_recovery_sizing": "disabled (not supported by design)",
    }


def update_config(
    s: Session,
    bot: Bot,
    new: dict[str, Any],
    actor: str,
    env: Environment,
    confirm_text: str | None,
    note: str = "",
) -> BotConfig:
    model = config_model(bot.kind)
    try:
        cfg = model.model_validate(new)
    except ValidationError as exc:
        raise bad_request(
            "invalid bot configuration",
            "validation_error",
            errors=[{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()],
        ) from exc
    if env is Environment.LIVE:
        require_confirm(
            confirm_text, "CONFIRM", "changing a bot configuration in live mode needs confirmation"
        )
    cur = current_config_row(s, bot.id)
    if cur.config == cfg.model_dump(mode="json"):
        return cur
    row = BotConfig(
        bot_id=bot.id,
        version=cur.version + 1,
        config=cfg.model_dump(mode="json"),
        created_by=actor,
        note=note[:300],
    )
    s.add(row)
    audit(
        s,
        "configuration",
        f"{bot.name} configuration v{row.version} saved",
        actor=actor,
        bot_id=bot.id,
        environment=env.value,
        data={"before": cur.config, "after": row.config},
    )
    return row


def apply_action(
    s: Session, bot: Bot, action: str, actor: str, env: Environment, confirm_text: str | None
) -> Bot:
    try:
        target = next_status(BotStatus(bot.status), action)
    except InvalidBotTransition as exc:
        raise APIError(
            409,
            "invalid_transition",
            str(exc),
            {"status": bot.status, "allowed_actions": allowed_actions(BotStatus(bot.status))},
        ) from exc
    if action == "start":
        if env is Environment.LIVE:
            require_confirm(
                confirm_text, "START LIVE", f"starting {bot.name} will place REAL orders on the live account"
            )
    if action == "resume" and bot.status == BotStatus.RISK_HALTED.value:
        blocking = [b for b in active_breakers(s, env) if b.critical and b.applies_to(bot.id, "*")]
        if blocking:
            raise APIError(
                409,
                "risk_halt_active",
                "clear the blocking circuit breakers before resuming",
                {"breakers": [f"{b.type.value}: {b.reason}" for b in blocking]},
            )
    before = bot.status
    bot.status = target.value
    bot.enabled = target is not BotStatus.DISABLED
    bot.row_version += 1
    if action in ("start", "resume"):
        bot.last_error = None
    audit(
        s,
        "bot_lifecycle",
        f"{bot.name}: {action} ({before} -> {target.value})",
        "info",
        bot_id=bot.id,
        actor=actor,
        environment=env.value,
    )
    kind = {"start": "bot_started", "stop": "bot_stopped", "pause": "bot_paused"}.get(action)
    if kind:
        notify(s, kind, f"{bot.name}: {before} -> {target.value} (by {actor})", bot_id=bot.id)
    return bot


def bot_out(s: Session, bot: Bot, env: Environment) -> dict[str, Any]:
    cfg = current_config(s, bot)
    cfg_row = current_config_row(s, bot.id)
    risk, _ = current_risk_config(s)
    active = (
        s.scalar(
            select(func.count())
            .select_from(TradingCycle)
            .where(
                TradingCycle.bot_id == bot.id,
                TradingCycle.active.is_(True),
                TradingCycle.environment == env.value,
            )
        )
        or 0
    )
    open_pos = (
        s.scalar(
            select(func.count())
            .select_from(PositionLeg)
            .join(TradingCycle)
            .where(
                TradingCycle.bot_id == bot.id,
                PositionLeg.status == "open",
                TradingCycle.environment == env.value,
            )
        )
        or 0
    )
    state = s.get(RiskState, env.value)
    since: datetime | None = state.day_start_at if state else None
    q = select(func.coalesce(func.sum(TradingCycle.realized_pnl), 0.0)).where(
        TradingCycle.bot_id == bot.id,
        TradingCycle.environment == env.value,
        TradingCycle.closed_at.is_not(None),
    )
    lifetime = float(s.scalar(q) or 0.0)
    today = float(s.scalar(q.where(TradingCycle.closed_at >= since)) or 0.0) if since else 0.0
    floating = float(
        s.scalar(
            select(func.coalesce(func.sum(PositionLeg.floating_pnl), 0.0))
            .join(TradingCycle)
            .where(
                TradingCycle.bot_id == bot.id,
                PositionLeg.status == "open",
                TradingCycle.environment == env.value,
            )
        )
        or 0.0
    )
    return {
        "id": bot.id,
        "name": bot.name,
        "kind": bot.kind,
        "strategy_name": bot.strategy_name,
        "strategy_version": bot.strategy_version,
        "status": bot.status,
        "enabled": bot.enabled,
        "environment": env.value,
        "instruments": list(cfg.instruments),
        "timeframe": cfg.timeframe if isinstance(cfg, DailyTradingConfig) else cfg.volatility.timeframe,
        "max_risk_pct": RiskEngine(risk).entry_risk_pct(BotKind(bot.kind), env),
        "active_cycles": int(active),
        "open_positions": int(open_pos),
        "daily_pnl": round(today + floating, 2),
        "lifetime_pnl": round(lifetime, 2),
        "last_evaluation_at": bot.last_evaluation_at,
        "last_execution_at": bot.last_execution_at,
        "last_error": bot.last_error,
        "magic": bot.magic,
        "config_version": cfg_row.version,
        "allowed_actions": allowed_actions(BotStatus(bot.status)),
        "updated_at": bot.updated_at or utcnow(),
    }
