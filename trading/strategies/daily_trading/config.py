from __future__ import annotations

from datetime import time

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.market_data.models import TIMEFRAME_MINUTES


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TrendFollowingParams(_M):
    ema_fast: int = Field(50, ge=2, le=500, description="Fast EMA period (trend filter).")
    ema_slow: int = Field(200, ge=3, le=1000, description="Slow EMA period (trend filter).")
    rsi_period: int = Field(14, ge=2, le=100, description="RSI period (momentum confirmation).")
    rsi_threshold: float = Field(
        50.0, ge=1, le=99, description="Long: RSI crosses above this. Short: crosses below 100 - this."
    )
    atr_period: int = Field(14, ge=2, le=200)
    stop_atr_mult: float = Field(1.5, gt=0, le=20, description="Initial stop = ATR x this.")
    target_atr_mult: float = Field(2.0, gt=0, le=50, description="Take-profit = ATR x this.")
    min_stop_pips: float = Field(8.0, gt=0, le=500)
    max_stop_pips: float = Field(50.0, gt=0, le=1000)

    @model_validator(mode="after")
    def _check(self) -> TrendFollowingParams:
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be smaller than ema_slow")
        if self.min_stop_pips > self.max_stop_pips:
            raise ValueError("min_stop_pips must not exceed max_stop_pips")
        return self


class SessionConfig(_M):
    start: time = Field(time(7, 0), description="No entries before this time (UTC).")
    end: time = Field(time(16, 0), description="No entries after this time (UTC).")
    close_time: time | None = Field(
        time(20, 0), description="Close any open daily trade at this time (UTC). Null = never."
    )
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], description="0=Mon ... 6=Sun")

    @model_validator(mode="after")
    def _check(self) -> SessionConfig:
        if self.start >= self.end:
            raise ValueError("session start must be before end")
        if self.close_time is not None and self.close_time < self.end:
            raise ValueError("close_time must be at or after the entry cutoff")
        if not set(self.weekdays) <= set(range(7)):
            raise ValueError("weekdays must be 0..6")
        return self


class BreakEvenConfig(_M):
    enabled: bool = False
    trigger_r: float = Field(
        1.0, gt=0, le=10, description="Move stop to break-even after price moves this many R in favour."
    )
    offset_pips: float = Field(0.5, ge=0, le=50)


class DailyTrailingConfig(_M):
    enabled: bool = False
    distance_atr_mult: float = Field(
        1.0, gt=0, le=20, description="Trailing distance = entry ATR x this (fixed for the trade)."
    )
    activate_r: float = Field(
        1.0, ge=0, le=10, description="Start trailing after price moves this many R in favour."
    )


class DailyTradingConfig(_M):
    instruments: list[str] = Field(default_factory=lambda: ["EURUSD", "GBPUSD"])
    strategy_name: str = Field("trend_following_baseline")
    timeframe: str = Field("M15")
    params: TrendFollowingParams = Field(default_factory=TrendFollowingParams)
    session: SessionConfig = Field(default_factory=SessionConfig)
    max_trades_per_day: int = Field(3, ge=1, le=50)
    cooldown_minutes: int = Field(
        30, ge=0, le=1440, description="Minimum time between entries on the same instrument."
    )
    max_holding_minutes: int = Field(480, ge=1, le=10080)
    break_even: BreakEvenConfig = Field(default_factory=BreakEvenConfig)
    trailing: DailyTrailingConfig = Field(default_factory=DailyTrailingConfig)
    est_slippage_points: float = Field(2.0, ge=0, le=100)

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        if v not in TIMEFRAME_MINUTES:
            raise ValueError(f"unsupported timeframe {v}")
        return v

    @field_validator("instruments")
    @classmethod
    def _instr(cls, v: list[str]) -> list[str]:
        if not v or len(set(v)) != len(v):
            raise ValueError("instruments must be a non-empty list without duplicates")
        return v
