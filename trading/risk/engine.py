"""Central risk engine.

Every entry from every bot goes through ``RiskEngine.evaluate``. The engine is
pure: it receives a snapshot of the portfolio and returns a decision with the
result of every individual check. Persisting the decision and reserving the
approved risk atomically is the caller's job (see the API service layer),
which is how two bots in different processes are prevented from both
consuming the same risk budget.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from trading.instruments.conversion import ConversionUnavailable, RateBook
from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import BotKind, Environment, Side
from trading.market_data.models import Quote
from trading.risk.circuit_breakers import ActiveBreaker
from trading.risk.config import RiskConfig
from trading.risk.daily import daily_loss, drawdown_pct
from trading.risk.sizing import LegPlan, SizingResult, size_position


@dataclass(frozen=True)
class OpenRiskItem:
    """An active cycle / trade that currently consumes risk budget."""

    bot_id: str
    bot_kind: BotKind
    ref_id: str  # cycle or trade id
    symbol: str
    base_currency: str
    quote_currency: str
    planned_risk: float  # account currency
    correlation_group: str | None = None


@dataclass
class PortfolioSnapshot:
    account_currency: str
    equity: float
    balance: float
    day_start_equity: float
    peak_equity: float
    free_margin: float | None = None
    used_margin: float | None = None
    open_items: list[OpenRiskItem] = field(default_factory=list)
    active_breakers: list[ActiveBreaker] = field(default_factory=list)
    consecutive_losses: dict[str, int] = field(default_factory=dict)  # bot_id -> count
    bot_realized_today: dict[str, float] = field(default_factory=dict)  # bot_id -> realized P/L today


@dataclass(frozen=True)
class TradeProposal:
    bot_id: str
    bot_kind: BotKind
    environment: Environment
    symbol: str
    legs: list[LegPlan]
    quote: Quote
    now: datetime
    commission_per_lot_side: float = 0.0
    est_slippage_points: float = 1.0
    margin_estimate_per_lot: float | None = None  # account ccy, per leg per lot


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class RiskDecision:
    approved: bool
    volume: float
    planned_risk: float
    planned_risk_pct: float
    risk_limit_pct: float
    checks: list[Check]
    legs: list[dict]

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]

    def as_dict(self) -> dict:
        return {
            "approved": self.approved,
            "volume": self.volume,
            "planned_risk": round(self.planned_risk, 2),
            "planned_risk_pct": round(self.planned_risk_pct, 4),
            "risk_limit_pct": self.risk_limit_pct,
            "checks": [asdict(c) for c in self.checks],
            "legs": self.legs,
            "reasons": self.reasons,
        }


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    # Effective per-entry risk % after environment caps.
    def entry_risk_pct(self, kind: BotKind, environment: Environment) -> float:
        env = self.config.env(environment)
        if kind is BotKind.NEWS_VOLATILITY:
            pct = self.config.news_volatility.max_cycle_risk_pct
            if env.max_cycle_risk_pct is not None:
                pct = min(pct, env.max_cycle_risk_pct)
        else:
            pct = self.config.daily_trading.max_trade_risk_pct
            if env.max_trade_risk_pct is not None:
                pct = min(pct, env.max_trade_risk_pct)
        return pct

    def daily_loss_limit_pct(self, environment: Environment) -> float:
        pct = self.config.global_.max_daily_loss_pct
        env = self.config.env(environment)
        if env.max_daily_loss_pct is not None:
            pct = min(pct, env.max_daily_loss_pct)
        return pct

    def evaluate(
        self,
        p: TradeProposal,
        pf: PortfolioSnapshot,
        spec: InstrumentSpec | None,
        rates: RateBook | None,
    ) -> RiskDecision:
        g = self.config.global_
        checks: list[Check] = []

        def add(name: str, ok: bool, detail: str) -> bool:
            checks.append(Check(name, ok, detail))
            return ok

        risk_pct = self.entry_risk_pct(p.bot_kind, p.environment)

        # 1. circuit breakers
        blocking = [b for b in pf.active_breakers if b.applies_to(p.bot_id, p.symbol)]
        add(
            "circuit_breakers",
            not blocking,
            "none active"
            if not blocking
            else "; ".join(f"{b.type.value} ({b.scope.value}): {b.reason}" for b in blocking),
        )

        # 2. instrument spec
        if spec is None:
            add("instrument_spec", False, f"no validated specification for {p.symbol}")
            return self._finish(checks, None, risk_pct, pf, p)
        override = self.config.instruments.get(p.symbol)
        add(
            "instrument_enabled",
            spec.tradable and (override is None or override.enabled),
            "tradable"
            if spec.tradable and (override is None or override.enabled)
            else "instrument disabled or not tradable",
        )

        # 3. quote freshness
        age = (p.now - p.quote.time).total_seconds()
        add(
            "quote_freshness",
            -2 <= age <= g.max_quote_age_seconds,
            f"quote age {age:.1f}s (limit {g.max_quote_age_seconds}s)",
        )

        # 4. spread
        spread_pips = spec.price_to_pips(p.quote.spread)
        limit = self.config.max_spread_pips(p.symbol)
        add("spread", spread_pips <= limit + 1e-9, f"spread {spread_pips:.2f} pips (limit {limit})")

        # 5. stop validity
        stop_ok = True
        for leg in p.legs:
            ref = p.quote.bid if leg.side is Side.BUY else p.quote.ask
            dist = (ref - leg.stop) if leg.side is Side.BUY else (leg.stop - ref)
            if dist <= spec.min_stop_distance or dist <= 0:
                stop_ok = False
                add(
                    "stop_distance",
                    False,
                    f"{leg.side.value} stop {leg.stop} too close: {spec.price_to_pips(dist):.1f} pips from trigger side (broker minimum {spec.price_to_pips(spec.min_stop_distance):.1f})",
                )
        if stop_ok:
            add("stop_distance", True, "all stops valid for broker minimum distance")

        # 6. sizing (requires conversion rates)
        sizing: SizingResult | None = None
        if rates is None:
            add("conversion", False, "conversion rates unavailable; automatic sizing blocked")
        else:
            try:
                max_vol = override.max_volume if override else None
                sizing = size_position(
                    spec,
                    p.legs,
                    pf.equity,
                    risk_pct,
                    pf.account_currency,
                    rates,
                    p.commission_per_lot_side,
                    p.est_slippage_points,
                    max_vol,
                )
                add("conversion", True, f"{spec.quote_currency}->{pf.account_currency} available")
                add(
                    "position_size",
                    sizing.volume > 0,
                    sizing.reason
                    or f"{sizing.volume} lots per leg, planned loss {sizing.planned_risk:.2f} {pf.account_currency} (budget {sizing.budget:.2f})",
                )
            except ConversionUnavailable as exc:
                add("conversion", False, f"{exc}; automatic sizing blocked")
            except ValueError as exc:
                add("position_size", False, str(exc))

        planned = sizing.planned_risk if sizing and sizing.volume > 0 else 0.0

        # 7. daily loss
        dl = daily_loss(pf.day_start_equity, pf.equity)
        dl_limit = pf.day_start_equity * self.daily_loss_limit_pct(p.environment) / 100
        add(
            "daily_loss",
            dl + planned <= dl_limit + 1e-9,
            f"daily loss {dl:.2f} + planned {planned:.2f} vs limit {dl_limit:.2f} {pf.account_currency}",
        )

        # 8. drawdown
        dd = drawdown_pct(pf.peak_equity, pf.equity)
        add("drawdown", dd < g.max_drawdown_pct, f"drawdown {dd:.2f}% (limit {g.max_drawdown_pct}%)")

        # 9. total open risk
        open_risk = sum(i.planned_risk for i in pf.open_items)
        cap = pf.equity * g.max_total_open_risk_pct / 100
        add(
            "total_open_risk",
            open_risk + planned <= cap + 1e-9,
            f"open risk {open_risk:.2f} + planned {planned:.2f} vs cap {cap:.2f} {pf.account_currency}",
        )

        # 10. concurrency
        add(
            "concurrent_cycles_global",
            len(pf.open_items) < g.max_concurrent_cycles,
            f"{len(pf.open_items)} active across both bots (limit {g.max_concurrent_cycles})",
        )
        mine = [i for i in pf.open_items if i.bot_id == p.bot_id]
        if p.bot_kind is BotKind.NEWS_VOLATILITY:
            lim = self.config.news_volatility.max_concurrent_cycles
            add("concurrent_cycles_bot", len(mine) < lim, f"{len(mine)} active for this bot (limit {lim})")
            same = [i for i in mine if i.symbol == p.symbol]
            ilim = self.config.news_volatility.max_cycles_per_instrument
            add("cycles_per_instrument", len(same) < ilim, f"{len(same)} active on {p.symbol} (limit {ilim})")
        else:
            lim = self.config.daily_trading.max_concurrent_trades
            add("concurrent_cycles_bot", len(mine) < lim, f"{len(mine)} open for this bot (limit {lim})")

        # 11. currency risk (aggregates both bots and correlated pairs sharing a currency)
        for ccy in (spec.base_currency, spec.quote_currency):
            used = sum(i.planned_risk for i in pf.open_items if ccy in (i.base_currency, i.quote_currency))
            ccap = pf.equity * g.max_currency_risk_pct / 100
            add(
                f"currency_risk_{ccy}",
                used + planned <= ccap + 1e-9,
                f"{ccy} risk {used:.2f} + {planned:.2f} vs cap {ccap:.2f}",
            )

        if spec.correlation_group:
            n = sum(1 for i in pf.open_items if i.correlation_group == spec.correlation_group)
            add(
                "correlation_group",
                n < g.max_cycles_per_correlation_group,
                f"{n} active in {spec.correlation_group} (limit {g.max_cycles_per_correlation_group})",
            )

        # 12. consecutive losses
        cl = pf.consecutive_losses.get(p.bot_id, 0)
        add(
            "consecutive_losses",
            cl < g.max_consecutive_losses,
            f"{cl} consecutive losses (pause at {g.max_consecutive_losses})",
        )

        # 13. bot-level daily realized loss
        bot_limit_pct = (
            self.config.news_volatility.max_daily_loss_pct
            if p.bot_kind is BotKind.NEWS_VOLATILITY
            else self.config.daily_trading.max_daily_loss_pct
        )
        bot_loss = max(0.0, -pf.bot_realized_today.get(p.bot_id, 0.0))
        bot_cap = pf.day_start_equity * bot_limit_pct / 100
        add(
            "bot_daily_loss",
            bot_loss + planned <= bot_cap + 1e-9,
            f"bot loss today {bot_loss:.2f} + planned {planned:.2f} vs {bot_cap:.2f}",
        )

        # 14. margin
        if (
            sizing
            and sizing.volume > 0
            and p.margin_estimate_per_lot is not None
            and pf.free_margin is not None
        ):
            need = p.margin_estimate_per_lot * sizing.volume * len(p.legs)
            used = (pf.used_margin or 0.0) + need
            level = pf.equity / used * 100 if used > 0 else float("inf")
            add(
                "margin",
                need <= pf.free_margin and level >= g.min_margin_level_pct,
                f"needs {need:.2f}, free {pf.free_margin:.2f}, post-trade margin level {level:.0f}% (min {g.min_margin_level_pct}%)",
            )
        else:
            add(
                "margin",
                True,
                "no broker margin estimate available; simulated margin checks apply at execution",
            )

        return self._finish(checks, sizing, risk_pct, pf, p)

    def _finish(
        self,
        checks: list[Check],
        sizing: SizingResult | None,
        risk_pct: float,
        pf: PortfolioSnapshot,
        p: TradeProposal,
    ) -> RiskDecision:
        approved = all(c.passed for c in checks) and sizing is not None and sizing.volume > 0
        legs = []
        if sizing:
            for lr in sizing.legs:
                legs.append(
                    {
                        "side": lr.side.value,
                        "stop_distance_pips": round(lr.stop_distance_pips, 2),
                        "loss_per_lot": round(lr.loss_per_lot, 2),
                        "planned_loss": round(lr.loss_per_lot * sizing.volume, 2),
                        "cost_per_lot": round(lr.cost_per_lot, 2),
                    }
                )
        planned = sizing.planned_risk if sizing and sizing.volume > 0 else 0.0
        return RiskDecision(
            approved=approved,
            volume=sizing.volume if (sizing and approved) else 0.0,
            planned_risk=planned,
            planned_risk_pct=planned / pf.equity * 100 if pf.equity > 0 else 0.0,
            risk_limit_pct=risk_pct,
            checks=checks,
            legs=legs,
        )


def evaluate_halts(cfg: RiskConfig, pf: PortfolioSnapshot, environment: Environment) -> list[tuple[str, str]]:
    """Account-level halt conditions that must trip a durable risk halt.

    Returns list of (breaker_type, reason).
    """
    out = []
    eng = RiskEngine(cfg)
    dl = daily_loss(pf.day_start_equity, pf.equity)
    lim = pf.day_start_equity * eng.daily_loss_limit_pct(environment) / 100
    if dl >= lim > 0:
        out.append(
            (
                "daily_loss_limit",
                f"daily loss {dl:.2f} reached limit {lim:.2f} {pf.account_currency} ({eng.daily_loss_limit_pct(environment)}% of {pf.day_start_equity:.2f})",
            )
        )
    dd = drawdown_pct(pf.peak_equity, pf.equity)
    if dd >= cfg.global_.max_drawdown_pct:
        out.append(("max_drawdown", f"drawdown {dd:.2f}% reached limit {cfg.global_.max_drawdown_pct}%"))
    return out
