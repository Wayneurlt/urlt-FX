from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading.interfaces.types import BotKind
from trading.market_data.models import TIMEFRAME_MINUTES


class CostModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spread_mode: Literal["data", "fixed"] = Field(
        "data", description="Use the dataset's spread column, or a fixed spread."
    )
    fixed_spread_pips: float = Field(1.0, ge=0, le=100)
    spread_multiplier: float = Field(
        1.0, ge=0.1, le=20, description="Stress multiplier applied to the spread."
    )
    min_spread_pips: float = Field(
        0.1, ge=0, le=50, description="Floor applied when data spread is missing/zero."
    )
    slippage_points: float = Field(
        1.0, ge=0, le=500, description="Fixed adverse slippage (ticks) on market fills and stop fills."
    )
    random_slippage_points: float = Field(
        2.0, ge=0, le=500, description="Additional uniform random adverse slippage 0..N ticks (seeded)."
    )
    second_leg_extra_slippage_points: float = Field(
        1.0,
        ge=0,
        le=500,
        description="Dual-entry: extra adverse slippage on the second leg (models latency between the two orders).",
    )
    commission_per_lot_side: float = Field(
        3.5, ge=0, le=100, description="Account currency per 1.0 lot per side."
    )
    swap_long_per_lot_day: float = Field(-0.5, ge=-100, le=100)
    swap_short_per_lot_day: float = Field(-0.3, ge=-100, le=100)


class ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latency_bars: int = Field(
        0, ge=0, le=10, description="Signals on bar t close fill at the open of bar t+1+latency_bars."
    )
    reject_probability: float = Field(0.0, ge=0, le=0.5)
    partial_fill_probability: float = Field(0.0, ge=0, le=1)
    partial_fill_ratio: float = Field(0.5, gt=0, le=1)
    leverage: float = Field(30.0, gt=0, le=1000)
    max_gap_bars: int = Field(
        5, ge=1, le=1000, description="Bars missing beyond this count as a data gap (no entries across it)."
    )


class RiskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_per_entry_pct: float = Field(
        0.5, gt=0, le=2.0, description="Dual-entry: combined risk of both legs. Daily: risk per trade."
    )
    max_daily_loss_pct: float = Field(1.5, gt=0, le=5.0)
    max_consecutive_losses: int = Field(
        3,
        ge=1,
        le=10,
        description="Pause new entries for the rest of the day after this many consecutive losses.",
    )
    max_spread_pips: float = Field(
        3.0, gt=0, le=50, description="Entries are skipped when the spread at the fill bar exceeds this."
    )


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_kind: BotKind
    strategy_version: str = "1.0.0"
    symbol: str
    base_timeframe: str = Field("M1", description="Resolution of the price data used for execution.")
    start: datetime
    end: datetime
    initial_balance: float = Field(10_000, gt=0)
    account_currency: str = "USD"
    conversion_rates: dict[str, float] = Field(
        default_factory=dict,
        description="Fixed rates (e.g. {'EURUSD': 1.08}) used when conversion can't be derived from the traded pair. Recorded as an assumption.",
    )
    costs: CostModel = Field(default_factory=CostModel)
    execution: ExecutionModel = Field(default_factory=ExecutionModel)
    risk: RiskModel = Field(default_factory=RiskModel)
    strategy: dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy config (NewsVolatilityConfig or DailyTradingConfig fields).",
    )
    seed: int = 1
    baseline_mode: Literal["none", "random_direction", "no_trailing"] = Field(
        "none",
        description="Baseline variants for comparison: random entry direction (daily) or trailing disabled (dual-entry).",
    )
    data_source: str = "synthetic"
    dataset_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> BacktestConfig:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start/end must be timezone-aware (UTC)")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.base_timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"unsupported base timeframe {self.base_timeframe}")
        return self
