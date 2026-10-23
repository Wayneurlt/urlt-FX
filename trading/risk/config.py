"""Risk configuration.

Values here are *demo-testing defaults*, not recommendations for live trading.
``HARD_LIMITS`` are non-negotiable ceilings that no configuration, strategy
proposal or AI suggestion can exceed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading.interfaces.types import Environment

HARD_LIMITS = {
    "max_cycle_risk_pct": 2.0,
    "max_trade_risk_pct": 2.0,
    "max_daily_loss_pct": 5.0,
    "max_drawdown_pct": 25.0,
    "max_total_open_risk_pct": 5.0,
    "max_concurrent_cycles": 10,
    "max_consecutive_losses": 10,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GlobalRiskConfig(_Strict):
    max_daily_loss_pct: float = Field(
        1.5,
        gt=0,
        le=HARD_LIMITS["max_daily_loss_pct"],
        description="Halt new entries when equity falls this % below the daily starting-equity reference.",
    )
    max_drawdown_pct: float = Field(
        5.0,
        gt=0,
        le=HARD_LIMITS["max_drawdown_pct"],
        description="Halt when equity falls this % below its peak.",
    )
    max_total_open_risk_pct: float = Field(
        1.0,
        gt=0,
        le=HARD_LIMITS["max_total_open_risk_pct"],
        description="Sum of planned loss-at-stop of all open cycles/trades from both bots, % of equity.",
    )
    max_concurrent_cycles: int = Field(
        2,
        ge=1,
        le=HARD_LIMITS["max_concurrent_cycles"],
        description="Active cycles + daily trades across both bots.",
    )
    max_currency_risk_pct: float = Field(
        0.75, gt=0, le=5.0, description="Planned risk of all exposure involving one currency, % of equity."
    )
    max_cycles_per_correlation_group: int = Field(
        2, ge=1, le=10, description="Active cycles per correlation group (e.g. JPY crosses)."
    )
    max_consecutive_losses: int = Field(
        3,
        ge=1,
        le=HARD_LIMITS["max_consecutive_losses"],
        description="Pause new entries for a bot after this many consecutive losing cycles/trades.",
    )
    max_quote_age_seconds: float = Field(
        5.0, gt=0, le=120, description="Quotes older than this block new entries."
    )
    default_max_spread_pips: float = Field(
        3.0, gt=0, le=50, description="Spread limit when no instrument override exists."
    )
    abnormal_slippage_pips: float = Field(
        5.0, gt=0, le=100, description="Fill slippage above this trips a circuit breaker."
    )
    max_order_rejections: int = Field(
        3, ge=1, le=20, description="Consecutive order rejections before the breaker trips."
    )
    min_margin_level_pct: float = Field(
        300.0,
        ge=100,
        le=5000,
        description="Post-trade margin level must stay above this (when the broker reports margin).",
    )
    daily_reset_hour_utc: int = Field(
        0, ge=0, le=23, description="Hour (UTC) when the daily-loss reference resets."
    )
    halt_policy: Literal["manage_existing", "close_all"] = Field(
        "manage_existing", description="What happens to open positions when a risk halt triggers."
    )
    unmatched_leg_policy: Literal["close", "keep_protected"] = Field(
        "close", description="Dual-entry: what to do when only one leg could be opened."
    )
    unprotected_position_policy: Literal["close"] = Field(
        "close", description="A position without a verified stop is always closed."
    )


class NewsBotRiskConfig(_Strict):
    max_cycle_risk_pct: float = Field(
        0.5,
        gt=0,
        le=HARD_LIMITS["max_cycle_risk_pct"],
        description="Combined planned loss of BOTH legs at their stops, % of equity.",
    )
    max_cycles_per_instrument: int = Field(
        1, ge=1, le=3, description="Active dual-entry cycles per instrument."
    )
    max_concurrent_cycles: int = Field(
        2, ge=1, le=HARD_LIMITS["max_concurrent_cycles"], description="Active cycles for this bot."
    )
    max_daily_loss_pct: float = Field(
        1.0,
        gt=0,
        le=HARD_LIMITS["max_daily_loss_pct"],
        description="Bot-level realized daily loss limit, % of daily reference equity.",
    )


class DailyBotRiskConfig(_Strict):
    max_trade_risk_pct: float = Field(
        0.25,
        gt=0,
        le=HARD_LIMITS["max_trade_risk_pct"],
        description="Planned loss at stop per trade, % of equity.",
    )
    max_concurrent_trades: int = Field(
        2, ge=1, le=HARD_LIMITS["max_concurrent_cycles"], description="Open trades for this bot."
    )
    max_daily_loss_pct: float = Field(
        1.0,
        gt=0,
        le=HARD_LIMITS["max_daily_loss_pct"],
        description="Bot-level realized daily loss limit, % of daily reference equity.",
    )


class InstrumentRiskOverride(_Strict):
    enabled: bool = True
    max_spread_pips: float | None = Field(None, gt=0, le=50)
    max_volume: float | None = Field(None, gt=0)


class EnvironmentLimits(_Strict):
    """Caps applied on top of bot settings per environment (tighter wins)."""

    max_cycle_risk_pct: float | None = Field(None, gt=0, le=HARD_LIMITS["max_cycle_risk_pct"])
    max_trade_risk_pct: float | None = Field(None, gt=0, le=HARD_LIMITS["max_trade_risk_pct"])
    max_daily_loss_pct: float | None = Field(None, gt=0, le=HARD_LIMITS["max_daily_loss_pct"])


def _default_env_limits() -> dict[str, EnvironmentLimits]:
    return {
        Environment.SIMULATION.value: EnvironmentLimits(),
        Environment.PAPER.value: EnvironmentLimits(),
        Environment.LIVE.value: EnvironmentLimits(
            max_cycle_risk_pct=0.5, max_trade_risk_pct=0.25, max_daily_loss_pct=1.5
        ),
    }


class RiskConfig(_Strict):
    global_: GlobalRiskConfig = Field(default_factory=GlobalRiskConfig, alias="global")
    news_volatility: NewsBotRiskConfig = Field(default_factory=NewsBotRiskConfig)
    daily_trading: DailyBotRiskConfig = Field(default_factory=DailyBotRiskConfig)
    instruments: dict[str, InstrumentRiskOverride] = Field(default_factory=dict)
    environments: dict[str, EnvironmentLimits] = Field(default_factory=_default_env_limits)

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)

    @model_validator(mode="after")
    def _cross_checks(self) -> RiskConfig:
        g = self.global_
        if self.news_volatility.max_cycle_risk_pct > g.max_total_open_risk_pct:
            raise ValueError(
                "news_volatility.max_cycle_risk_pct cannot exceed global.max_total_open_risk_pct"
            )
        if self.daily_trading.max_trade_risk_pct > g.max_total_open_risk_pct:
            raise ValueError("daily_trading.max_trade_risk_pct cannot exceed global.max_total_open_risk_pct")
        if (
            self.news_volatility.max_daily_loss_pct > g.max_daily_loss_pct
            or self.daily_trading.max_daily_loss_pct > g.max_daily_loss_pct
        ):
            raise ValueError("bot daily-loss limits cannot exceed the global daily-loss limit")
        if self.news_volatility.max_concurrent_cycles > g.max_concurrent_cycles:
            raise ValueError(
                "news_volatility.max_concurrent_cycles cannot exceed global.max_concurrent_cycles"
            )
        if self.daily_trading.max_concurrent_trades > g.max_concurrent_cycles:
            raise ValueError("daily_trading.max_concurrent_trades cannot exceed global.max_concurrent_cycles")
        if g.max_daily_loss_pct > g.max_drawdown_pct:
            raise ValueError("global.max_daily_loss_pct cannot exceed global.max_drawdown_pct")
        for k in self.environments:
            Environment(k)
        return self

    def env(self, environment: Environment | str) -> EnvironmentLimits:
        return self.environments.get(str(Environment(environment).value), EnvironmentLimits())

    def max_spread_pips(self, symbol: str) -> float:
        o = self.instruments.get(symbol)
        return o.max_spread_pips if o and o.max_spread_pips else self.global_.default_max_spread_pips

    def dump(self) -> dict:
        return self.model_dump(by_alias=True)


def risk_increases(before: RiskConfig, after: RiskConfig) -> list[str]:
    """List settings whose change *loosens* risk (needs explicit human approval)."""
    loosen: list[str] = []
    pairs = [
        ("global.max_daily_loss_pct", before.global_.max_daily_loss_pct, after.global_.max_daily_loss_pct),
        ("global.max_drawdown_pct", before.global_.max_drawdown_pct, after.global_.max_drawdown_pct),
        (
            "global.max_total_open_risk_pct",
            before.global_.max_total_open_risk_pct,
            after.global_.max_total_open_risk_pct,
        ),
        (
            "global.max_concurrent_cycles",
            before.global_.max_concurrent_cycles,
            after.global_.max_concurrent_cycles,
        ),
        (
            "global.max_currency_risk_pct",
            before.global_.max_currency_risk_pct,
            after.global_.max_currency_risk_pct,
        ),
        (
            "global.max_consecutive_losses",
            before.global_.max_consecutive_losses,
            after.global_.max_consecutive_losses,
        ),
        (
            "global.max_quote_age_seconds",
            before.global_.max_quote_age_seconds,
            after.global_.max_quote_age_seconds,
        ),
        (
            "global.default_max_spread_pips",
            before.global_.default_max_spread_pips,
            after.global_.default_max_spread_pips,
        ),
        (
            "news_volatility.max_cycle_risk_pct",
            before.news_volatility.max_cycle_risk_pct,
            after.news_volatility.max_cycle_risk_pct,
        ),
        (
            "news_volatility.max_daily_loss_pct",
            before.news_volatility.max_daily_loss_pct,
            after.news_volatility.max_daily_loss_pct,
        ),
        (
            "daily_trading.max_trade_risk_pct",
            before.daily_trading.max_trade_risk_pct,
            after.daily_trading.max_trade_risk_pct,
        ),
        (
            "daily_trading.max_daily_loss_pct",
            before.daily_trading.max_daily_loss_pct,
            after.daily_trading.max_daily_loss_pct,
        ),
    ]
    for name, a, b in pairs:
        if b > a:
            loosen.append(name)
    if before.global_.min_margin_level_pct > after.global_.min_margin_level_pct:
        loosen.append("global.min_margin_level_pct")

    def raised_cap(old: float | None, new: float | None) -> bool:
        # None = no cap. Removing a cap or raising it loosens risk.
        return old is not None and (new is None or new > old)

    for env in sorted(set(before.environments) | set(after.environments)):
        b_env, a_env = (
            before.environments.get(env, EnvironmentLimits()),
            after.environments.get(env, EnvironmentLimits()),
        )
        for field in EnvironmentLimits.model_fields:
            if raised_cap(getattr(b_env, field), getattr(a_env, field)):
                loosen.append(f"environments.{env}.{field}")
    for sym in sorted(set(before.instruments) | set(after.instruments)):
        b_ins = before.instruments.get(sym, InstrumentRiskOverride())
        a_ins = after.instruments.get(sym, InstrumentRiskOverride())
        if before.max_spread_pips(sym) < after.max_spread_pips(sym):
            loosen.append(f"instruments.{sym}.max_spread_pips")
        if raised_cap(b_ins.max_volume, a_ins.max_volume):
            loosen.append(f"instruments.{sym}.max_volume")
        if not b_ins.enabled and a_ins.enabled:
            loosen.append(f"instruments.{sym}.enabled")
    return loosen
