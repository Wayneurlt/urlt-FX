"""Per-tick signal evaluation for both bots (no persistence, no execution).

Each call returns the candidate signals plus a diagnostics list explaining
every instrument that did NOT produce a signal, which is logged as the
"strategy evaluation" audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from trading.instruments.spec import InstrumentRegistry
from trading.interfaces.types import TriggerType
from trading.market_data.models import timeframe_delta
from trading.market_data.provider import MarketDataProvider
from trading.news.eligibility import eligible_candidates, feed_is_fresh
from trading.news.provider import CalendarProvider, CalendarUnavailable
from trading.strategies.base import StrategySignal
from trading.strategies.daily_trading.config import DailyTradingConfig
from trading.strategies.daily_trading.trade import in_entry_session
from trading.strategies.daily_trading.trend_following import build_daily_strategy
from trading.strategies.news_volatility.config import NewsVolatilityConfig
from trading.strategies.news_volatility.volatility import evaluate_volatility, required_bars


@dataclass
class EvaluationResult:
    signals: list[StrategySignal] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # conditions that blocked a whole entry mode


def evaluate_news_volatility(
    bot_id: str,
    cfg: NewsVolatilityConfig,
    registry: InstrumentRegistry,
    market: MarketDataProvider,
    calendar: CalendarProvider | None,
    now: datetime,
    processed_keys: set[str],
    last_volatility_trigger: dict[str, datetime],
) -> EvaluationResult:
    res = EvaluationResult()
    specs = [registry.get(s) for s in cfg.instruments if registry.has(s)]
    for s in cfg.instruments:
        if not registry.has(s):
            res.diagnostics.append(
                {"symbol": s, "mode": "any", "result": "skipped", "reason": "instrument not in registry"}
            )

    if cfg.entry_mode.news:
        if calendar is None:
            res.blocked.append("news entries blocked: no calendar provider configured")
        else:
            fresh, why = feed_is_fresh(calendar.health(now), now, cfg.news)
            if not fresh:
                res.blocked.append(f"news entries blocked: {why}")
            else:
                try:
                    window = timedelta(
                        seconds=max(cfg.news.pre_event_seconds, cfg.news.post_event_seconds) + 60
                    )
                    events = calendar.events(now - window, now + window)
                    for cand in eligible_candidates(bot_id, events, specs, now, cfg.news, processed_keys):
                        res.signals.append(
                            StrategySignal(
                                symbol=cand.symbol,
                                trigger=TriggerType.NEWS,
                                dedupe_key=cand.dedupe_key,
                                candle_time=None,
                                event_id=cand.event.event_id,
                                details={
                                    "event": cand.event.name,
                                    "currency": cand.event.currency,
                                    "impact": cand.event.impact.value,
                                    "scheduled_at": cand.event.scheduled_at.isoformat(),
                                    "origin": cand.event.origin.value,
                                    "window": [cand.window_start.isoformat(), cand.window_end.isoformat()],
                                },
                            )
                        )
                except CalendarUnavailable as exc:
                    res.blocked.append(f"news entries blocked: {exc}")

    if cfg.entry_mode.volatility:
        vc = cfg.volatility
        tf = timeframe_delta(vc.timeframe)
        lookback = tf * (required_bars(vc) * 3)  # extra history so Wilder ATR converges
        for spec in specs:
            if any(sg.symbol == spec.symbol for sg in res.signals):
                continue
            last = last_volatility_trigger.get(spec.symbol)
            if last and now - last < timedelta(minutes=vc.cooldown_minutes):
                res.diagnostics.append(
                    {
                        "symbol": spec.symbol,
                        "mode": "volatility",
                        "result": "cooldown",
                        "reason": f"last trigger {last.isoformat()}",
                    }
                )
                continue
            try:
                candles = market.get_candles(spec.symbol, vc.timeframe, now - lookback, now)
            except Exception as exc:  # noqa: BLE001 - provider failure is reported, not fatal
                res.diagnostics.append(
                    {
                        "symbol": spec.symbol,
                        "mode": "volatility",
                        "result": "error",
                        "reason": f"market data unavailable: {exc}",
                    }
                )
                continue
            vs = evaluate_volatility(candles, now, spec, vc)
            if not vs.triggered:
                failed = [r.name for r in vs.rules if not r.passed]
                res.diagnostics.append(
                    {
                        "symbol": spec.symbol,
                        "mode": "volatility",
                        "result": "no_signal",
                        "reason": f"rules not met: {', '.join(failed)}",
                        "rules": vs.as_dict()["rules"],
                    }
                )
                continue
            key = f"vol:{bot_id}:{spec.symbol}:{vs.candle_time.isoformat() if vs.candle_time else now.isoformat()}"
            if key in processed_keys:
                continue
            res.signals.append(
                StrategySignal(
                    symbol=spec.symbol,
                    trigger=TriggerType.VOLATILITY,
                    dedupe_key=key,
                    candle_time=vs.candle_time,
                    details=vs.as_dict(),
                )
            )
    return res


def evaluate_daily(
    bot_id: str,
    cfg: DailyTradingConfig,
    registry: InstrumentRegistry,
    market: MarketDataProvider,
    now: datetime,
    processed_keys: set[str],
) -> EvaluationResult:
    res = EvaluationResult()
    ok, why = in_entry_session(now, cfg)
    if not ok:
        res.blocked.append(f"outside entry session: {why}")
        return res
    strat = build_daily_strategy(cfg.strategy_name, cfg.params, cfg.timeframe, bot_id)
    tf = timeframe_delta(cfg.timeframe)
    lookback = tf * (strat.required_bars() * 3)
    for sym in cfg.instruments:
        if not registry.has(sym):
            res.diagnostics.append(
                {"symbol": sym, "result": "skipped", "reason": "instrument not in registry"}
            )
            continue
        spec = registry.get(sym)
        try:
            candles = market.get_candles(sym, cfg.timeframe, now - lookback, now)
        except Exception as exc:  # noqa: BLE001
            res.diagnostics.append(
                {"symbol": sym, "result": "error", "reason": f"market data unavailable: {exc}"}
            )
            continue
        ev = strat.evaluate(sym, spec, candles, now)
        if ev.signal is None:
            res.diagnostics.append(
                {"symbol": sym, "result": "no_signal", "reason": ev.reason, "indicators": ev.details}
            )
            continue
        if ev.signal.dedupe_key in processed_keys:
            continue
        res.signals.append(ev.signal)
    return res
