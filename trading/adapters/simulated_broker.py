"""Simulated broker.

Implements ``BrokerAdapter`` with the same request/response models a real
broker adapter uses. Every result it produces carries ``simulated=True``.

Execution model (documented in docs/BACKTESTING.md and docs/TRADING_RULES.md):

* Market BUY fills at ask (+ adverse slippage), market SELL at bid (- slippage).
* Quotes come from a ``MarketDataProvider`` evaluated at ``now + latency``.
* Stops are checked whenever ``process_market`` runs. A triggered stop is
  executed as a market order at the current executable price (bid for long,
  ask for short) minus adverse slippage, so gaps through the stop are filled
  at the worse price, never at the stop price by assumption.
* Commission is charged per lot per side in account currency.
* Faults can be injected for testing via ``inject``.

The full state is JSON-serialisable (``to_state``/``from_state``) so it can be
persisted between processes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from trading.adapters.base import (
    AccountInfo,
    BrokerAdapter,
    BrokerDisconnected,
    Capabilities,
    ConnectionStatus,
    Deal,
    DealEntry,
    DealReason,
    InstrumentUnavailable,
    InsufficientMargin,
    InvalidStop,
    InvalidVolume,
    OrderLookup,
    OrderRejected,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    PositionNotFound,
    StopModifyResult,
    UnknownOrderOutcome,
)
from trading.instruments.conversion import ConversionUnavailable
from trading.instruments.spec import InstrumentRegistry, InstrumentSpec
from trading.interfaces.types import MarginMode, Side
from trading.market_data.models import Candle, Quote
from trading.market_data.provider import MarketDataProvider, MarketStatus


@dataclass
class SimConfig:
    account_currency: str = "USD"
    initial_balance: float = 10_000.0
    leverage: float = 30.0
    margin_mode: MarginMode = MarginMode.HEDGING
    commission_per_lot_side: float = 3.5  # account currency, per 1.0 lot per side
    slippage_points_max: int = 3  # uniform 0..N ticks adverse slippage on market fills
    extra_spread_points: int = 0
    latency_ms: int = 50
    reject_probability: float = 0.0
    partial_fill_probability: float = 0.0
    partial_fill_ratio: float = 0.5
    swap_long_per_lot_day: float = -0.5
    swap_short_per_lot_day: float = -0.3
    seed: int = 42
    stop_out_margin_level: float = 50.0


@dataclass
class _SimPosition:
    position_id: str
    symbol: str
    side: str
    volume: float
    open_price: float
    open_time: str
    sl: float | None
    tp: float | None
    magic: int
    client_order_id: str
    commission: float = 0.0
    swap: float = 0.0


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class SimulatedBroker(BrokerAdapter):
    name = "simulated"
    is_simulated = True

    def __init__(
        self,
        market: MarketDataProvider,
        clock: Callable[[], datetime],
        config: SimConfig | None = None,
        registry: InstrumentRegistry | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        self.market = market
        self.clock = clock
        self.config = config or SimConfig()
        self.registry = registry or InstrumentRegistry.with_defaults()
        # Fault injection for tests: list of directives consumed in order.
        #   "reject" | "timeout_filled" | "timeout_not_filled" | "partial:<ratio>"
        #   | "reject_sl" (order fills but SL not attached) | "disconnect"
        self.inject: list[str] = []
        self.inject_modify: list[str] = []  # "reject" | "timeout"
        self._load(state)

    # --- state -------------------------------------------------------------
    def _load(self, state: dict[str, Any] | None) -> None:
        if state:
            self.balance = float(state["balance"])
            self.positions = {p["position_id"]: _SimPosition(**p) for p in state["positions"]}
            self.deals: list[dict] = state["deals"]
            self.orders: dict[str, dict] = state["orders"]
            self.seq = int(state["seq"])
            self.rng_counter = int(state["rng_counter"])
            self.connected = bool(state.get("connected", True))
            self.last_swap_day = state.get("last_swap_day")
        else:
            self.balance = self.config.initial_balance
            self.positions = {}
            self.deals = []
            self.orders = {}
            self.seq = 0
            self.rng_counter = 0
            self.connected = True
            self.last_swap_day = None

    def to_state(self) -> dict[str, Any]:
        return {
            "balance": self.balance,
            "positions": [asdict(p) for p in self.positions.values()],
            "deals": self.deals,
            "orders": self.orders,
            "seq": self.seq,
            "rng_counter": self.rng_counter,
            "connected": self.connected,
            "last_swap_day": self.last_swap_day,
        }

    def _next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"SIM-{prefix}-{self.seq}"

    def _rand(self) -> float:
        """Deterministic uniform [0,1) stream derived from the seed."""
        self.rng_counter += 1
        h = hashlib.blake2b(f"{self.config.seed}:{self.rng_counter}".encode(), digest_size=8).digest()
        return int.from_bytes(h, "big") / 2**64

    # --- helpers -----------------------------------------------------------
    def _require_connected(self) -> None:
        if not self.connected:
            raise BrokerDisconnected("simulated broker is disconnected")

    def _spec(self, symbol: str) -> InstrumentSpec:
        try:
            spec = self.registry.get(symbol)
        except KeyError as exc:
            raise InstrumentUnavailable(f"unknown symbol {symbol}") from exc
        if not spec.tradable:
            raise InstrumentUnavailable(f"{symbol} is not tradable")
        return spec

    def _quote(self, symbol: str, latency: bool = False) -> Quote:
        now = self.clock()
        if latency and self.config.latency_ms:
            now = now + timedelta(milliseconds=self.config.latency_ms)
        q = self.market.get_quote(symbol, now)
        if self.config.extra_spread_points:
            spec = self._spec(symbol)
            q = Quote(
                q.symbol,
                q.bid,
                spec.round_price(q.ask + self.config.extra_spread_points * spec.tick_size),
                q.time,
                q.source,
            )
        return q

    def _slip(self, spec: InstrumentSpec) -> float:
        n = int(self._rand() * (self.config.slippage_points_max + 1))
        return n * spec.tick_size

    def _to_account(self, amount_quote: float, spec: InstrumentSpec) -> float:
        rates = self.market.rates(self.clock())
        try:
            return amount_quote * rates.rate(spec.quote_currency, self.config.account_currency)
        except ConversionUnavailable:
            raise

    def _pnl(self, p: _SimPosition, exit_price: float, volume: float) -> float:
        spec = self._spec(p.symbol)
        sign = 1 if p.side == Side.BUY.value else -1
        return self._to_account((exit_price - p.open_price) * sign * spec.contract_size * volume, spec)

    def _margin_for(self, spec: InstrumentSpec, volume: float, price: float) -> float:
        notional_base = spec.contract_size * volume
        rates = self.market.rates(self.clock())
        notional_acct = notional_base * rates.rate(spec.base_currency, self.config.account_currency)
        return notional_acct / self.config.leverage

    def _floating(self) -> float:
        total = 0.0
        for p in self.positions.values():
            q = self._quote(p.symbol)
            px = q.bid if p.side == Side.BUY.value else q.ask
            total += self._pnl(p, px, p.volume) + p.swap
        return total

    def _used_margin(self) -> float:
        total = 0.0
        for p in self.positions.values():
            spec = self._spec(p.symbol)
            total += self._margin_for(spec, p.volume, p.open_price)
        return total

    def _to_position(self, p: _SimPosition) -> Position:
        q = self._quote(p.symbol)
        px = q.bid if p.side == Side.BUY.value else q.ask
        return Position(
            position_id=p.position_id,
            symbol=p.symbol,
            side=Side(p.side),
            volume=p.volume,
            open_price=p.open_price,
            open_time=_dt(p.open_time),
            sl=p.sl,
            tp=p.tp,
            magic=p.magic,
            client_order_id=p.client_order_id,
            profit=self._pnl(p, px, p.volume),
            swap=p.swap,
            commission=p.commission,
            simulated=True,
        )

    def _record_deal(
        self,
        p: _SimPosition,
        side: Side,
        volume: float,
        price: float,
        entry: DealEntry,
        reason: DealReason,
        profit: float,
        commission: float,
        order_id: str | None,
        swap: float = 0.0,
    ) -> dict:
        d = {
            "deal_id": self._next_id("D"),
            "position_id": p.position_id,
            "order_id": order_id,
            "symbol": p.symbol,
            "side": side.value,
            "volume": volume,
            "price": price,
            "time": _iso(self.clock()),
            "entry": entry.value,
            "reason": reason.value,
            "profit": profit,
            "commission": commission,
            "swap": swap,
            "magic": p.magic,
            "client_order_id": p.client_order_id,
        }
        self.deals.append(d)
        return d

    def _validate_stop(self, spec: InstrumentSpec, side: Side, sl: float, q: Quote) -> None:
        if side is Side.BUY:
            limit = q.bid - spec.min_stop_distance
            if sl >= limit:
                raise InvalidStop(
                    f"BUY stop {sl} must be below {limit} (bid {q.bid}, min distance {spec.min_stop_distance})"
                )
        else:
            limit = q.ask + spec.min_stop_distance
            if sl <= limit:
                raise InvalidStop(
                    f"SELL stop {sl} must be above {limit} (ask {q.ask}, min distance {spec.min_stop_distance})"
                )

    # --- BrokerAdapter: connection & account -------------------------------
    def connect(self) -> ConnectionStatus:
        self.connected = True
        return ConnectionStatus(True, "simulated broker", self.clock())

    def disconnect(self) -> None:
        self.connected = False

    def check_connection(self) -> ConnectionStatus:
        return ConnectionStatus(
            self.connected, "simulated broker" if self.connected else "disconnected", self.clock()
        )

    def account_info(self) -> AccountInfo:
        self._require_connected()
        equity = self.balance + self._floating()
        margin = self._used_margin()
        return AccountInfo(
            account_id="SIM-PAPER",
            currency=self.config.account_currency,
            balance=round(self.balance, 2),
            equity=round(equity, 2),
            margin=round(margin, 2),
            free_margin=round(equity - margin, 2),
            margin_level=round(equity / margin * 100, 2) if margin > 0 else None,
            leverage=self.config.leverage,
            margin_mode=self.config.margin_mode,
            is_simulated=True,
            is_demo=True,
            server="local-simulator",
        )

    def capabilities(self) -> Capabilities:
        hedging = self.config.margin_mode is MarginMode.HEDGING
        return Capabilities(
            hedging=hedging,
            broker_side_stops=True,
            partial_fills_possible=self.config.partial_fill_probability > 0,
            client_order_ids=True,
            notes=["simulated execution"],
        )

    def instrument_spec(self, symbol: str) -> InstrumentSpec:
        return self._spec(symbol)

    def get_quote(self, symbol: str) -> Quote:
        self._require_connected()
        self._spec(symbol)
        return self._quote(symbol)

    def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        self._require_connected()
        return self.market.get_candles(symbol, timeframe, start, end)

    def market_status(self, symbol: str) -> MarketStatus:
        return self.market.market_status(symbol, self.clock())

    def estimate_margin(self, symbol: str, side: Side, volume: float, price: float) -> float | None:
        return self._margin_for(self._spec(symbol), volume, price)

    # --- orders ------------------------------------------------------------
    def submit_market_order(self, request: OrderRequest) -> OrderResult:
        self._require_connected()
        existing = self.orders.get(request.client_order_id)
        if existing is not None:
            # Idempotent: same client id never creates a second position.
            return self._order_result(existing)

        directive = self.inject.pop(0) if self.inject else None
        if directive == "disconnect":
            self.connected = False
            raise BrokerDisconnected("connection lost while submitting")
        if directive == "timeout_not_filled":
            raise UnknownOrderOutcome("request timed out (simulated)")

        spec = self._spec(request.symbol)
        now = self.clock()
        status = self.market.market_status(request.symbol, now)
        if not status.open:
            return self._reject(request, f"market closed: {status.reason}")
        if not spec.is_valid_volume(request.volume):
            raise InvalidVolume(
                f"volume {request.volume} invalid for {spec.symbol} (min {spec.min_volume}, step {spec.volume_step})"
            )
        if directive == "reject" or (
            self.config.reject_probability and self._rand() < self.config.reject_probability
        ):
            return self._reject(request, "rejected by simulated dealer")

        q = self._quote(request.symbol, latency=True)
        if request.sl is not None and directive != "reject_sl":
            self._validate_stop(spec, request.side, request.sl, q)

        volume = request.volume
        if directive and directive.startswith("partial:"):
            volume = spec.normalize_volume(request.volume * float(directive.split(":")[1]))
        elif self.config.partial_fill_probability and self._rand() < self.config.partial_fill_probability:
            volume = spec.normalize_volume(request.volume * self.config.partial_fill_ratio)
        if volume <= 0:
            return self._reject(request, "partial fill below minimum volume")

        margin_needed = self._margin_for(spec, volume, q.mid)
        acct = self.account_info()
        if margin_needed > acct.free_margin:
            raise InsufficientMargin(
                f"margin required {margin_needed:.2f} > free margin {acct.free_margin:.2f}"
            )

        slip = self._slip(spec)
        price = spec.round_price(q.ask + slip) if request.side is Side.BUY else spec.round_price(q.bid - slip)
        commission = -self.config.commission_per_lot_side * volume
        order_id = self._next_id("O")

        if self.config.margin_mode is MarginMode.NETTING:
            pos = self._net(request, spec, volume, price, commission, order_id)
        else:
            pos = _SimPosition(
                position_id=self._next_id("P"),
                symbol=request.symbol,
                side=request.side.value,
                volume=volume,
                open_price=price,
                open_time=_iso(now),
                sl=None if directive == "reject_sl" else request.sl,
                tp=request.tp,
                magic=request.magic,
                client_order_id=request.client_order_id,
                commission=commission,
            )
            self.positions[pos.position_id] = pos
            self.balance += commission
            self._record_deal(
                pos, request.side, volume, price, DealEntry.IN, DealReason.CLIENT, 0.0, commission, order_id
            )

        rec = {
            "client_order_id": request.client_order_id,
            "status": (
                OrderStatus.FILLED if volume >= request.volume else OrderStatus.PARTIALLY_FILLED
            ).value,
            "broker_order_id": order_id,
            "position_id": pos.position_id if pos else None,
            "requested_volume": request.volume,
            "filled_volume": volume,
            "fill_price": price,
            "sl": pos.sl if pos else None,
            "time": _iso(now),
            "reject_reason": None,
            "commission": commission,
        }
        self.orders[request.client_order_id] = rec
        if directive == "timeout_filled":
            raise UnknownOrderOutcome("request timed out after reaching the simulated dealer")
        return self._order_result(rec)

    def _net(
        self,
        request: OrderRequest,
        spec: InstrumentSpec,
        volume: float,
        price: float,
        commission: float,
        order_id: str,
    ) -> _SimPosition | None:
        """Netting account: an opposing order reduces/closes the existing position."""
        existing = next((p for p in self.positions.values() if p.symbol == request.symbol), None)
        self.balance += commission
        if existing is None or existing.side == request.side.value:
            if existing is None:
                existing = _SimPosition(
                    self._next_id("P"),
                    request.symbol,
                    request.side.value,
                    0.0,
                    price,
                    _iso(self.clock()),
                    request.sl,
                    request.tp,
                    request.magic,
                    request.client_order_id,
                )
                self.positions[existing.position_id] = existing
            total = existing.volume + volume
            existing.open_price = spec.round_price(
                (existing.open_price * existing.volume + price * volume) / total
            )
            existing.volume = round(total, 8)
            existing.sl = request.sl
            self._record_deal(
                existing,
                request.side,
                volume,
                price,
                DealEntry.IN,
                DealReason.CLIENT,
                0.0,
                commission,
                order_id,
            )
            return existing
        close_vol = min(existing.volume, volume)
        profit = self._pnl(existing, price, close_vol)
        self.balance += profit
        self._record_deal(
            existing,
            request.side,
            close_vol,
            price,
            DealEntry.OUT,
            DealReason.CLIENT,
            profit,
            commission,
            order_id,
        )
        existing.volume = round(existing.volume - close_vol, 8)
        if existing.volume <= 1e-9:
            del self.positions[existing.position_id]
        return existing if existing.volume > 0 else None

    def _reject(self, request: OrderRequest, reason: str) -> OrderResult:
        rec = {
            "client_order_id": request.client_order_id,
            "status": OrderStatus.REJECTED.value,
            "broker_order_id": None,
            "position_id": None,
            "requested_volume": request.volume,
            "filled_volume": 0.0,
            "fill_price": None,
            "sl": None,
            "time": _iso(self.clock()),
            "reject_reason": reason,
            "commission": 0.0,
        }
        self.orders[request.client_order_id] = rec
        raise OrderRejected(reason, details={"client_order_id": request.client_order_id})

    def _order_result(self, rec: dict) -> OrderResult:
        return OrderResult(
            client_order_id=rec["client_order_id"],
            status=OrderStatus(rec["status"]),
            broker_order_id=rec["broker_order_id"],
            position_id=rec["position_id"],
            requested_volume=rec["requested_volume"],
            filled_volume=rec["filled_volume"],
            fill_price=rec["fill_price"],
            sl=rec["sl"],
            time=_dt(rec["time"]),
            reject_reason=rec["reject_reason"],
            commission=rec["commission"],
            simulated=True,
        )

    def modify_stop(self, position_id: str, sl: float, tp: float | None = None) -> StopModifyResult:
        self._require_connected()
        p = self.positions.get(position_id)
        if p is None:
            raise PositionNotFound(position_id)
        directive = self.inject_modify.pop(0) if self.inject_modify else None
        if directive == "timeout":
            raise UnknownOrderOutcome("stop modification timed out")
        if directive == "reject":
            return StopModifyResult(position_id, False, p.sl, "rejected by simulated dealer")
        spec = self._spec(p.symbol)
        q = self._quote(p.symbol)
        sl = spec.round_price(sl)
        if p.sl is not None and spec.freeze_level > 0:
            ref = q.bid if p.side == Side.BUY.value else q.ask
            if abs(ref - p.sl) <= spec.freeze_level:
                return StopModifyResult(position_id, False, p.sl, "within freeze level")
        try:
            self._validate_stop(spec, Side(p.side), sl, q)
        except InvalidStop as exc:
            return StopModifyResult(position_id, False, p.sl, str(exc))
        p.sl = sl
        if tp is not None:
            p.tp = tp
        return StopModifyResult(position_id, True, p.sl)

    def close_position(
        self, position_id: str, volume: float | None = None, client_order_id: str = ""
    ) -> OrderResult:
        self._require_connected()
        if client_order_id and client_order_id in self.orders:
            return self._order_result(self.orders[client_order_id])
        p = self.positions.get(position_id)
        if p is None:
            raise PositionNotFound(position_id)
        return self._close(
            p, volume, DealReason.CLIENT, client_order_id or f"close-{position_id}-{self.seq + 1}"
        )

    def _close(
        self,
        p: _SimPosition,
        volume: float | None,
        reason: DealReason,
        client_order_id: str,
        price: float | None = None,
    ) -> OrderResult:
        spec = self._spec(p.symbol)
        vol = p.volume if volume is None else min(volume, p.volume)
        if price is None:
            q = self._quote(p.symbol, latency=reason is DealReason.CLIENT)
            slip = self._slip(spec)
            price = (
                spec.round_price(q.bid - slip) if p.side == Side.BUY.value else spec.round_price(q.ask + slip)
            )
        profit = self._pnl(p, price, vol)
        commission = -self.config.commission_per_lot_side * vol
        swap_part = p.swap * (vol / p.volume) if p.volume else 0.0
        self.balance += profit + commission + swap_part
        order_id = self._next_id("O")
        close_side = Side(p.side).opposite
        self._record_deal(
            p, close_side, vol, price, DealEntry.OUT, reason, profit, commission, order_id, swap_part
        )
        p.swap -= swap_part
        p.volume = round(p.volume - vol, 8)
        if p.volume <= 1e-9:
            del self.positions[p.position_id]
        rec = {
            "client_order_id": client_order_id,
            "status": OrderStatus.FILLED.value,
            "broker_order_id": order_id,
            "position_id": p.position_id,
            "requested_volume": vol,
            "filled_volume": vol,
            "fill_price": price,
            "sl": None,
            "time": _iso(self.clock()),
            "reject_reason": None,
            "commission": commission,
        }
        self.orders[client_order_id] = rec
        return self._order_result(rec)

    # --- queries -----------------------------------------------------------
    def list_positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        self._require_connected()
        return [
            self._to_position(p)
            for p in self.positions.values()
            if (symbol is None or p.symbol == symbol) and (magic is None or p.magic == magic)
        ]

    def list_orders(self) -> list[OrderResult]:
        return []  # market orders only: nothing rests on the book

    def order_history(self, since: datetime) -> list[OrderResult]:
        self._require_connected()
        return [self._order_result(o) for o in self.orders.values() if _dt(o["time"]) >= since]

    def deal_history(self, since: datetime, position_id: str | None = None) -> list[Deal]:
        self._require_connected()
        return [
            self._deal(d)
            for d in self.deals
            if _dt(d["time"]) >= since and (not position_id or d["position_id"] == position_id)
        ]

    @staticmethod
    def _deal(d: dict) -> Deal:
        return Deal(
            deal_id=d["deal_id"],
            position_id=d["position_id"],
            order_id=d["order_id"],
            symbol=d["symbol"],
            side=Side(d["side"]),
            volume=d["volume"],
            price=d["price"],
            time=_dt(d["time"]),
            entry=DealEntry(d["entry"]),
            reason=DealReason(d["reason"]),
            profit=d["profit"],
            commission=d["commission"],
            swap=d.get("swap", 0.0),
            magic=d["magic"],
            client_order_id=d["client_order_id"],
            simulated=True,
        )

    def find_order(self, client_order_id: str) -> OrderLookup:
        self._require_connected()
        rec = self.orders.get(client_order_id)
        return OrderLookup(client_order_id, rec is not None, self._order_result(rec) if rec else None)

    # --- market processing -------------------------------------------------
    def process_market(self) -> list[Deal]:
        """Evaluate stops/TPs against current quotes. Returns closing deals."""
        if not self.connected:
            return []
        before = len(self.deals)
        now = self.clock()
        self._apply_swap(now)
        for p in list(self.positions.values()):
            if not self.market.market_status(p.symbol, now).open:
                continue
            spec = self._spec(p.symbol)
            q = self._quote(p.symbol)
            if p.side == Side.BUY.value:
                exit_px = q.bid
                hit_sl = p.sl is not None and exit_px <= p.sl
                hit_tp = p.tp is not None and exit_px >= p.tp
            else:
                exit_px = q.ask
                hit_sl = p.sl is not None and exit_px >= p.sl
                hit_tp = p.tp is not None and exit_px <= p.tp
            if hit_sl:
                slip = self._slip(spec)
                px = (
                    spec.round_price(exit_px - slip)
                    if p.side == Side.BUY.value
                    else spec.round_price(exit_px + slip)
                )
                self._close(p, None, DealReason.STOP_LOSS, f"sl-{p.position_id}", price=px)
            elif hit_tp:
                self._close(p, None, DealReason.TAKE_PROFIT, f"tp-{p.position_id}", price=exit_px)
        # Margin stop-out
        if self.positions:
            acct = self.account_info()
            if acct.margin_level is not None and acct.margin_level < self.config.stop_out_margin_level:
                worst = min(self.positions.values(), key=lambda p: self._to_position(p).profit)
                self._close(worst, None, DealReason.STOP_OUT, f"so-{worst.position_id}")
        return [self._deal(d) for d in self.deals[before:]]

    def _apply_swap(self, now: datetime) -> None:
        rollover = (now - timedelta(hours=22)).date().isoformat()
        if self.last_swap_day is None:
            self.last_swap_day = rollover
            return
        if rollover == self.last_swap_day:
            return
        self.last_swap_day = rollover
        for p in self.positions.values():
            rate = (
                self.config.swap_long_per_lot_day
                if p.side == Side.BUY.value
                else self.config.swap_short_per_lot_day
            )
            p.swap += rate * p.volume

    def reset(self, initial_balance: float | None = None) -> None:
        if initial_balance is not None:
            self.config.initial_balance = initial_balance
        seq = self.seq  # keep ids unique across resets
        self._load(None)
        self.seq = seq


def sim_config_from_dict(d: dict[str, Any] | None) -> SimConfig:
    if not d:
        return SimConfig()
    d = dict(d)
    if "margin_mode" in d:
        d["margin_mode"] = MarginMode(d["margin_mode"])
    allowed = set(SimConfig.__dataclass_fields__)
    return SimConfig(**{k: v for k, v in d.items() if k in allowed})


__all__ = ["SimConfig", "SimulatedBroker", "field", "sim_config_from_dict"]
