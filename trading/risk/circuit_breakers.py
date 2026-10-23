from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BreakerType(StrEnum):
    EXCESSIVE_SPREAD = "excessive_spread"
    STALE_MARKET_DATA = "stale_market_data"
    REPEATED_ORDER_REJECTION = "repeated_order_rejection"
    UNCERTAIN_ORDER_STATE = "uncertain_order_state"
    BROKER_DISCONNECTED = "broker_disconnected"
    STOP_PLACEMENT_FAILURE = "stop_placement_failure"
    STOP_MODIFICATION_FAILURE = "stop_modification_failure"
    ABNORMAL_SLIPPAGE = "abnormal_slippage"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    MAX_DRAWDOWN = "max_drawdown"
    INVALID_INSTRUMENT_SPEC = "invalid_instrument_spec"
    CALENDAR_PROVIDER_FAILURE = "calendar_provider_failure"
    PERSISTENCE_FAILURE = "persistence_failure"
    DUPLICATE_SUBMISSION = "duplicate_submission"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    RECONCILIATION_FAILURE = "reconciliation_failure"


class BreakerScope(StrEnum):
    GLOBAL = "global"
    BOT = "bot"
    INSTRUMENT = "instrument"


@dataclass(frozen=True)
class BreakerMeta:
    critical: bool  # critical breakers require an explicit, authorised manual clear
    auto_clear: bool  # cleared automatically once the condition resolves
    recovery: str


BREAKER_META: dict[BreakerType, BreakerMeta] = {
    BreakerType.EXCESSIVE_SPREAD: BreakerMeta(
        False, True, "Clears automatically when the spread returns within the configured limit."
    ),
    BreakerType.STALE_MARKET_DATA: BreakerMeta(
        False, True, "Clears automatically when fresh quotes are received."
    ),
    BreakerType.REPEATED_ORDER_REJECTION: BreakerMeta(
        True, False, "Investigate the rejection reasons, then clear manually."
    ),
    BreakerType.UNCERTAIN_ORDER_STATE: BreakerMeta(
        True, False, "Reconcile positions with the broker and clear manually once the state is known."
    ),
    BreakerType.BROKER_DISCONNECTED: BreakerMeta(
        False, True, "Clears after reconnection and a successful reconciliation."
    ),
    BreakerType.STOP_PLACEMENT_FAILURE: BreakerMeta(
        True, False, "Verify every open position has a protective stop, then clear manually."
    ),
    BreakerType.STOP_MODIFICATION_FAILURE: BreakerMeta(
        True, False, "Verify the trailing stop state with the broker, then clear manually."
    ),
    BreakerType.ABNORMAL_SLIPPAGE: BreakerMeta(True, False, "Review execution quality, then clear manually."),
    BreakerType.DAILY_LOSS_LIMIT: BreakerMeta(
        True, False, "Clears at the next daily reset or by authorised manual clear."
    ),
    BreakerType.MAX_DRAWDOWN: BreakerMeta(True, False, "Requires an authorised manual clear after review."),
    BreakerType.INVALID_INSTRUMENT_SPEC: BreakerMeta(
        True, False, "Fix the instrument specification, then clear manually."
    ),
    BreakerType.CALENDAR_PROVIDER_FAILURE: BreakerMeta(
        False, True, "Clears automatically when the calendar feed is fresh again."
    ),
    BreakerType.PERSISTENCE_FAILURE: BreakerMeta(
        True, False, "Restore database health and reconcile, then clear manually."
    ),
    BreakerType.DUPLICATE_SUBMISSION: BreakerMeta(
        True, False, "Inspect the duplicate order intent, then clear manually."
    ),
    BreakerType.CONSECUTIVE_LOSSES: BreakerMeta(
        False, False, "Review recent results, then clear manually or wait for the next daily reset."
    ),
    BreakerType.RECONCILIATION_FAILURE: BreakerMeta(
        True, False, "Resolve the local/broker mismatch, then clear manually."
    ),
}


@dataclass(frozen=True)
class ActiveBreaker:
    type: BreakerType
    scope: BreakerScope
    scope_key: str | None  # bot id / symbol / None for global
    reason: str

    @property
    def critical(self) -> bool:
        return BREAKER_META[self.type].critical

    def applies_to(self, bot_id: str, symbol: str) -> bool:
        if self.scope is BreakerScope.GLOBAL:
            return True
        if self.scope is BreakerScope.BOT:
            return self.scope_key == bot_id
        return self.scope_key == symbol
