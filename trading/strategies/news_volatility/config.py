from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STRATEGY_NAME = "news_volatility_straddle"
STRATEGY_VERSION = "1.0.0"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EntryModeConfig(_M):
    news: bool = Field(True, description="Allow entries around eligible economic events.")
    volatility: bool = Field(
        True, description="Allow entries on independently detected volatility breakouts."
    )


class NewsWindowConfig(_M):
    currencies: list[str] = Field(
        default_factory=lambda: ["USD", "EUR", "GBP", "JPY"], description="Event currencies to trade."
    )
    impact_levels: list[str] = Field(
        default_factory=lambda: ["high"], description="Event impact levels to trade."
    )
    pre_event_seconds: int = Field(
        60, ge=0, le=3600, description="Entry window opens this many seconds before the scheduled release."
    )
    post_event_seconds: int = Field(
        30, ge=0, le=3600, description="Entry window closes this many seconds after the scheduled release."
    )
    max_feed_age_minutes: int = Field(
        720, ge=1, le=10080, description="Calendar data older than this blocks news entries."
    )

    @field_validator("currencies")
    @classmethod
    def _ccy(cls, v: list[str]) -> list[str]:
        out = [c.upper() for c in v]
        if any(len(c) != 3 for c in out):
            raise ValueError("currencies must be 3-letter codes")
        return out

    @field_validator("impact_levels")
    @classmethod
    def _impact(cls, v: list[str]) -> list[str]:
        allowed = {"low", "medium", "high"}
        if not set(v) <= allowed:
            raise ValueError(f"impact levels must be in {sorted(allowed)}")
        return v


class VolatilityConfig(_M):
    timeframe: str = Field("M1", description="Candle timeframe for the detector.")
    atr_period: int = Field(14, ge=2, le=200)
    atr_baseline_period: int = Field(
        100, ge=10, le=2000, description="ATR baseline = mean ATR over this many bars before the current one."
    )
    atr_ratio_min: float = Field(
        1.8, gt=1.0, le=20, description="Current ATR / baseline ATR must reach this."
    )
    range_lookback: int = Field(60, ge=5, le=2000)
    range_multiple_min: float = Field(
        2.5, gt=1.0, le=50, description="Last candle range / median range must reach this."
    )
    expansion_lookback: int = Field(5, ge=1, le=200)
    expansion_min_pips: float = Field(
        8.0, ge=0, le=500, description="|close - close N bars ago| must reach this many pips."
    )
    require_breakout: bool = Field(False, description="Also require a close beyond the prior N-bar high/low.")
    breakout_lookback: int = Field(20, ge=2, le=500)
    cooldown_minutes: int = Field(
        30, ge=0, le=1440, description="Minimum minutes between volatility triggers on one instrument."
    )
    max_candle_age_bars: int = Field(
        2, ge=1, le=10, description="Latest closed candle must be at most this many bars old."
    )

    @model_validator(mode="after")
    def _tf(self) -> VolatilityConfig:
        from trading.market_data.models import TIMEFRAME_MINUTES

        if self.timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"unsupported timeframe {self.timeframe}")
        return self


class TrailingConfig(_M):
    enabled: bool = Field(
        True,
        description="Activate a fixed-distance trailing stop on the surviving leg after the other leg is confirmed stopped out.",
    )
    distance_pips: float = Field(10.0, gt=0, le=500, description="Fixed trailing distance D.")
    min_step_pips: float = Field(
        0.5, ge=0, le=50, description="Skip stop modifications smaller than this (avoids broker spam)."
    )
    max_modify_retries: int = Field(3, ge=0, le=10)


class InstrumentOverride(_M):
    initial_stop_pips: float | None = Field(None, gt=0, le=1000)
    trailing_distance_pips: float | None = Field(None, gt=0, le=500)


class NewsVolatilityConfig(_M):
    instruments: list[str] = Field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY"])
    entry_mode: EntryModeConfig = Field(default_factory=EntryModeConfig)
    news: NewsWindowConfig = Field(default_factory=NewsWindowConfig)
    volatility: VolatilityConfig = Field(default_factory=VolatilityConfig)
    initial_stop_pips: float = Field(
        15.0,
        gt=0,
        le=1000,
        description="Initial stop distance for each leg, from that leg's executable entry side.",
    )
    trailing: TrailingConfig = Field(default_factory=TrailingConfig)
    max_cycle_duration_minutes: int = Field(
        240, ge=1, le=10080, description="Close the surviving leg after this long."
    )
    instrument_overrides: dict[str, InstrumentOverride] = Field(default_factory=dict)
    est_slippage_points: float = Field(
        2.0, ge=0, le=100, description="Slippage assumption used for risk sizing."
    )

    @field_validator("instruments")
    @classmethod
    def _instr(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one instrument is required")
        if len(set(v)) != len(v):
            raise ValueError("duplicate instruments")
        return v

    @model_validator(mode="after")
    def _modes(self) -> NewsVolatilityConfig:
        if not (self.entry_mode.news or self.entry_mode.volatility):
            raise ValueError("enable at least one entry mode (news or volatility)")
        return self

    def stop_pips(self, symbol: str) -> float:
        o = self.instrument_overrides.get(symbol)
        return o.initial_stop_pips if o and o.initial_stop_pips else self.initial_stop_pips

    def trailing_pips(self, symbol: str) -> float:
        o = self.instrument_overrides.get(symbol)
        return o.trailing_distance_pips if o and o.trailing_distance_pips else self.trailing.distance_pips
