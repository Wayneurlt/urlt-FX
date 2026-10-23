"""Instrument-aware position sizing.

For each leg the loss per 1.0 lot if the stop is hit is

    stop_loss_money = |entry - stop| * contract_size * rate(quote -> account)
    cost_money      = 2 * commission_per_lot_side
                      + 2 * slippage_points * tick_size * contract_size * rate(quote -> account)
    loss_per_lot    = stop_loss_money + cost_money

``entry`` is the executable side (ask for BUY, bid for SELL), so the spread
paid at entry is already inside ``|entry - stop|`` (stops trigger on the
opposite side of the book).

For a dual-entry cycle both legs can stop out (a whipsaw), so the cycle's
planned risk is the SUM of both legs. A single volume ``v`` is used for both
legs:

    v = floor_to_step(budget / (loss_per_lot_buy + loss_per_lot_sell))

Volumes are always rounded DOWN; if the result is below the broker minimum
the trade is rejected rather than rounded up.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading.instruments.conversion import RateBook
from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import Side


@dataclass(frozen=True)
class LegPlan:
    side: Side
    entry: float  # expected executable entry price
    stop: float


@dataclass(frozen=True)
class LegRisk:
    side: Side
    stop_distance: float
    stop_distance_pips: float
    loss_per_lot: float  # account currency, incl. costs
    cost_per_lot: float


@dataclass(frozen=True)
class SizingResult:
    volume: float  # 0.0 => cannot size within budget
    legs: list[LegRisk]
    planned_risk: float  # account currency, all legs at volume
    budget: float
    reason: str | None = None


def leg_risk(
    spec: InstrumentSpec,
    leg: LegPlan,
    account_ccy: str,
    rates: RateBook,
    commission_per_lot_side: float,
    slippage_points: float,
) -> LegRisk:
    dist = (leg.entry - leg.stop) if leg.side is Side.BUY else (leg.stop - leg.entry)
    if dist <= 0:
        raise ValueError(f"{leg.side} stop {leg.stop} is on the wrong side of entry {leg.entry}")
    rate = rates.rate(spec.quote_currency, account_ccy)
    stop_money = dist * spec.contract_size * rate
    cost = 2 * commission_per_lot_side + 2 * slippage_points * spec.tick_size * spec.contract_size * rate
    return LegRisk(leg.side, dist, spec.price_to_pips(dist), stop_money + cost, cost)


def size_position(
    spec: InstrumentSpec,
    legs: list[LegPlan],
    equity: float,
    risk_pct: float,
    account_ccy: str,
    rates: RateBook,
    commission_per_lot_side: float = 0.0,
    slippage_points: float = 0.0,
    max_volume: float | None = None,
) -> SizingResult:
    if equity <= 0:
        return SizingResult(0.0, [], 0.0, 0.0, "equity must be positive")
    risks = [leg_risk(spec, lg, account_ccy, rates, commission_per_lot_side, slippage_points) for lg in legs]
    budget = equity * risk_pct / 100
    per_lot = sum(r.loss_per_lot for r in risks)
    raw = budget / per_lot if per_lot > 0 else 0.0
    if max_volume is not None:
        raw = min(raw, max_volume)
    volume = spec.normalize_volume(raw)
    if volume <= 0:
        return SizingResult(
            0.0,
            risks,
            0.0,
            budget,
            f"risk budget {budget:.2f} {account_ccy} is too small for the minimum volume {spec.min_volume} "
            f"(needs {per_lot * spec.min_volume:.2f} {account_ccy})",
        )
    return SizingResult(volume, risks, per_lot * volume, budget)
