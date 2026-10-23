"""Fixed-distance trailing stop on executable-side quotes.

BUY:  SL_candidate = H_bid - D   (H_bid = highest bid since activation)
SELL: SL_candidate = L_ask + D   (L_ask = lowest ask since activation)

Updates are protective-only: a BUY stop only moves up, a SELL stop only moves
down. Candidates that violate broker constraints are clamped to the nearest
valid level, and only submitted if that is still an improvement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import Side
from trading.market_data.models import Quote


@dataclass
class TrailingState:
    side: Side
    distance: float  # price units
    water_mark: float  # H_bid for BUY, L_ask for SELL
    confirmed_sl: float | None
    activated_at: str  # ISO UTC
    failures: int = 0
    last_candidate: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TrailingState:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known["side"] = Side(known["side"])
        return cls(**known)


def activate(
    side: Side, quote: Quote, distance: float, confirmed_sl: float | None, activated_at: str
) -> TrailingState:
    mark = quote.bid if side is Side.BUY else quote.ask
    return TrailingState(side, distance, mark, confirmed_sl, activated_at)


def update_water_mark(state: TrailingState, quote: Quote) -> bool:
    """Move the water mark with the executable side. Returns True if it moved."""
    if state.side is Side.BUY:
        if quote.bid > state.water_mark:
            state.water_mark = quote.bid
            return True
    elif quote.ask < state.water_mark:
        state.water_mark = quote.ask
        return True
    return False


def raw_candidate(state: TrailingState) -> float:
    return state.water_mark - state.distance if state.side is Side.BUY else state.water_mark + state.distance


def is_protective_improvement(
    side: Side, current_sl: float | None, candidate: float, spec: InstrumentSpec, min_step: float = 0.0
) -> bool:
    if current_sl is None:
        return True
    if side is Side.BUY:
        return candidate > current_sl + max(min_step, spec.tick_size / 2)
    return candidate < current_sl - max(min_step, spec.tick_size / 2)


def next_stop(
    state: TrailingState, quote: Quote, spec: InstrumentSpec, min_step: float = 0.0
) -> float | None:
    """Return the stop to submit now, or None if no valid improvement exists."""
    update_water_mark(state, quote)
    cand = raw_candidate(state)
    if state.side is Side.BUY:
        cand = spec.round_price_down(cand)
        limit = spec.round_price_down(quote.bid - spec.min_stop_distance - spec.tick_size)
        cand = min(cand, limit)
        if (
            spec.freeze_level
            and state.confirmed_sl is not None
            and quote.bid - state.confirmed_sl <= spec.freeze_level
        ):
            return None
    else:
        cand = spec.round_price_up(cand)
        limit = spec.round_price_up(quote.ask + spec.min_stop_distance + spec.tick_size)
        cand = max(cand, limit)
        if (
            spec.freeze_level
            and state.confirmed_sl is not None
            and state.confirmed_sl - quote.ask <= spec.freeze_level
        ):
            return None
    state.last_candidate = cand
    if not is_protective_improvement(state.side, state.confirmed_sl, cand, spec, min_step):
        return None
    return cand
