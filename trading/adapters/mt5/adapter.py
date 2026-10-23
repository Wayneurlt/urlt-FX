"""MetaTrader 5 adapter (DISABLED until explicitly configured and validated).

Requires the official ``MetaTrader5`` Python package, which only runs on
Windows next to an installed, logged-in MT5 terminal. See docs/MT5_INTEGRATION.md.

Safety properties implemented here:
* account margin mode is detected (hedging vs netting) and reported;
* every order carries ``magic`` (bot id) and ``comment`` (client order id);
* a timed-out / ambiguous ``order_send`` raises ``UnknownOrderOutcome`` - the
  caller reconciles via ``find_order`` (history + open positions by comment)
  and never blindly retries;
* stops are validated against ``trade_stops_level`` / ``trade_freeze_level``;
* broker-confirmed position and deal tickets are used as identifiers.

This module has NOT been exercised against a live terminal in this
repository's test suite; it is covered by unit tests with a fake ``mt5``
module only. Treat it as unverified until validated on a demo account.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from trading.adapters.base import (
    AccountInfo,
    BrokerAdapter,
    BrokerDisconnected,
    BrokerError,
    BrokerRateLimited,
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
from trading.instruments.spec import InstrumentSpec
from trading.interfaces.types import DataSource, MarginMode, Side
from trading.market_data.models import TIMEFRAME_MINUTES, Candle, Quote
from trading.market_data.provider import MarketStatus, fx_market_open

# MT5 trade return codes (subset)
RETCODE_DONE = 10009
RETCODE_DONE_PARTIAL = 10010
RETCODE_PLACED = 10008
RETCODE_REQUOTE = 10004
RETCODE_TIMEOUT = 10012
RETCODE_INVALID_VOLUME = 10014
RETCODE_INVALID_STOPS = 10016
RETCODE_NO_MONEY = 10019
RETCODE_TOO_MANY_REQUESTS = 10024
RETCODE_CONNECTION = 10031
RETCODE_MARKET_CLOSED = 10018

MAX_CLOCK_SKEW = 30.0  # seconds of broker-vs-local clock drift we correct for

ACCOUNT_MARGIN_MODE_RETAIL_NETTING = 0
ACCOUNT_MARGIN_MODE_EXCHANGE = 1
ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2


@dataclass
class MT5Settings:
    login: int
    server: str
    password_secret: str  # resolved by the backend secret store; never logged
    terminal_path: str | None = None
    symbol_suffix: str = ""
    deviation_points: int = 20
    timeout_ms: int = 10_000


class MT5Unavailable(BrokerError):
    code = "mt5_unavailable"


def load_mt5_module() -> Any:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise MT5Unavailable(
            "the MetaTrader5 package is not installed (Windows + MT5 terminal required)"
        ) from exc


class MT5Adapter(BrokerAdapter):
    name = "mt5"
    is_simulated = False

    def __init__(self, settings: MT5Settings, mt5_module: Any | None = None) -> None:
        self.settings = settings
        self.mt5 = mt5_module
        self._connected = False
        # MT5 reports tick/bar/deal times in the trade server's timezone, not UTC.
        self._offset = timedelta(0)
        self.offset_verified = False
        # Seconds the broker's clock runs ahead of this machine's (learned from ticks, which
        # cannot genuinely be from the future). Corrected up to MAX_CLOCK_SKEW; beyond that it
        # is treated as a real clock/timezone fault and quotes are left uncorrected (and rejected).
        self.clock_skew = 0.0

    # --- connection ----------------------------------------------------------
    def connect(self) -> ConnectionStatus:
        """Attach to the terminal; log in only if it is not already on the configured account.

        Logging in again counts as an "account change" in MT5, and with the default
        terminal option that switches Algo Trading OFF - so avoid it when possible.
        """
        if self.mt5 is None:
            self.mt5 = load_mt5_module()
        base: dict[str, Any] = {"timeout": self.settings.timeout_ms}
        if self.settings.terminal_path:
            base["path"] = self.settings.terminal_path
        if not self.mt5.initialize(**base):
            code, msg = self.mt5.last_error()
            self._connected = False
            return ConnectionStatus(False, f"initialize failed ({code}): {msg}", datetime.now(UTC))
        if not self._on_configured_account():
            # initialize() does not switch accounts once attached; login() does.
            ok = self.mt5.login(
                self.settings.login, password=self.settings.password_secret, server=self.settings.server
            )
            if not ok or not self._on_configured_account():
                code, msg = self.mt5.last_error()
                self._connected = False
                return ConnectionStatus(False, f"login failed ({code}): {msg}", datetime.now(UTC))
        self._connected = True
        self._detect_server_offset()
        self.get_quote("EURUSD") if self.offset_verified else None  # learn clock skew from a fresh tick
        note = (
            f"server time UTC{self._offset_hours():+g}h, broker clock {self.clock_skew:+.1f}s vs this PC"
            + ("" if self.offset_verified else " (unverified: no fresh tick)")
        )
        return ConnectionStatus(True, f"connected; {note}", datetime.now(UTC))

    def _on_configured_account(self) -> bool:
        a = self.mt5.account_info() if self.mt5 is not None else None
        return bool(a and int(a.login) == int(self.settings.login) and str(a.server) == self.settings.server)

    def trading_allowed(self) -> bool:
        """False when the terminal's Algo Trading button is off: every order would be refused."""
        info = self.mt5.terminal_info() if self.mt5 is not None and self._connected else None
        return bool(info and info.trade_allowed)

    def _offset_hours(self) -> float:
        return self._offset.total_seconds() / 3600

    def _detect_server_offset(self) -> None:
        """Server-time offset = latest tick time - real UTC now, rounded to 30 minutes.

        Only trusted when the tick is fresh (market open); otherwise the offset stays
        at its previous value and ``offset_verified`` is False.
        """
        mt5 = self._req()
        candidates = [self._sym("EURUSD"), self._sym("GBPUSD"), self._sym("USDJPY")]
        for name in candidates:
            mt5.symbol_select(name, True)
            tick = mt5.symbol_info_tick(name)
            if tick is None or not tick.time:
                continue
            diff = float(tick.time) - time.time()
            rounded = round(diff / 1800) * 1800
            if abs(diff - rounded) <= 120 and abs(rounded) <= 14 * 3600:
                self._offset = timedelta(seconds=rounded)
                self.offset_verified = True
                return
        self.offset_verified = False

    def _utc(self, server_ts: float) -> datetime:
        """Convert an MT5 server timestamp (seconds) to a real UTC datetime."""
        return datetime.fromtimestamp(server_ts, UTC) - self._offset

    def _server(self, utc_dt: datetime) -> datetime:
        """Convert a UTC datetime into the server-time frame MT5 queries expect."""
        return utc_dt + self._offset

    def _filling(self, symbol_name: str) -> int:
        """Pick a filling mode the symbol allows (bit 1 = FOK, bit 2 = IOC), else RETURN."""
        mt5 = self._req()
        info = mt5.symbol_info(symbol_name)
        flags = int(getattr(info, "filling_mode", 0) or 0) if info is not None else 0
        if flags & 2:
            return int(mt5.ORDER_FILLING_IOC)
        if flags & 1:
            return int(mt5.ORDER_FILLING_FOK)
        return int(mt5.ORDER_FILLING_RETURN)

    def disconnect(self) -> None:
        if self.mt5 is not None:
            self.mt5.shutdown()
        self._connected = False

    def check_connection(self) -> ConnectionStatus:
        if self.mt5 is None or not self._connected:
            return ConnectionStatus(False, "not connected", datetime.now(UTC))
        info = self.mt5.terminal_info()
        ok = bool(info and info.connected) and self._on_configured_account()
        return ConnectionStatus(
            ok, "terminal connected" if ok else "terminal not connected to trade server", datetime.now(UTC)
        )

    def _req(self) -> Any:
        if self.mt5 is None or not self._connected:
            raise BrokerDisconnected("MT5 not connected")
        return self.mt5

    def _sym(self, symbol: str) -> str:
        return (
            symbol if symbol.endswith(self.settings.symbol_suffix) else symbol + self.settings.symbol_suffix
        )

    # --- account / instruments ---------------------------------------------
    def account_info(self) -> AccountInfo:
        a = self._req().account_info()
        if a is None:
            raise BrokerDisconnected("account_info unavailable")
        if int(a.login) != int(self.settings.login):
            # e.g. the terminal is mid-login or was switched by hand: its numbers are not ours.
            raise BrokerDisconnected(
                f"terminal is on account {a.login}, not the configured {self.settings.login}"
            )
        mode = {
            ACCOUNT_MARGIN_MODE_RETAIL_HEDGING: MarginMode.HEDGING,
            ACCOUNT_MARGIN_MODE_RETAIL_NETTING: MarginMode.NETTING,
            ACCOUNT_MARGIN_MODE_EXCHANGE: MarginMode.NETTING,
        }.get(a.margin_mode, MarginMode.UNKNOWN)
        return AccountInfo(
            account_id=str(a.login),
            currency=a.currency,
            balance=a.balance,
            equity=a.equity,
            margin=a.margin,
            free_margin=a.margin_free,
            margin_level=a.margin_level or None,
            leverage=float(a.leverage),
            margin_mode=mode,
            is_simulated=False,
            is_demo=a.trade_mode == 0,
            server=a.server,
        )

    def capabilities(self) -> Capabilities:
        acct = self.account_info()
        return Capabilities(
            hedging=acct.margin_mode is MarginMode.HEDGING,
            broker_side_stops=True,
            partial_fills_possible=True,
            client_order_ids=True,
            notes=[f"margin mode: {acct.margin_mode.value}"],
        )

    def instrument_spec(self, symbol: str) -> InstrumentSpec:
        mt5 = self._req()
        info = mt5.symbol_info(self._sym(symbol))
        if info is None:
            raise InstrumentUnavailable(f"symbol {symbol} not found at broker")
        if not info.visible:
            mt5.symbol_select(info.name, True)
        point = info.point
        pip = point * 10 if info.digits in (3, 5) else point
        return InstrumentSpec(
            symbol=symbol.removesuffix(self.settings.symbol_suffix)
            if self.settings.symbol_suffix
            else symbol,
            base_currency=info.currency_base,
            quote_currency=info.currency_profit,
            digits=info.digits,
            tick_size=info.trade_tick_size or point,
            pip_size=pip,
            contract_size=info.trade_contract_size,
            min_volume=info.volume_min,
            max_volume=info.volume_max,
            volume_step=info.volume_step,
            min_stop_distance=info.trade_stops_level * point,
            freeze_level=info.trade_freeze_level * point,
            tradable=info.trade_mode != 0,
            broker_symbol=info.name,
            source="broker",
        )

    def get_quote(self, symbol: str) -> Quote:
        t = self._req().symbol_info_tick(self._sym(symbol))
        if t is None:
            raise InstrumentUnavailable(f"no tick for {symbol}")
        ts = self._utc(t.time_msc / 1000)
        ahead = (ts - datetime.now(UTC)).total_seconds()
        # Track the drift: jump up to any fresh tick that looks ahead, otherwise decay slowly so a
        # later clock sync is followed (an over-estimate would make fresh prices look stale).
        observed = ahead if 0 < ahead <= MAX_CLOCK_SKEW else 0.0
        self.clock_skew = max(observed, self.clock_skew - 0.25)
        if 0 < self.clock_skew <= MAX_CLOCK_SKEW:
            ts -= timedelta(seconds=self.clock_skew)
        return Quote(symbol, t.bid, t.ask, ts, DataSource.LIVE)

    def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        mt5 = self._req()
        tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}")
        rates = mt5.copy_rates_range(self._sym(symbol), tf_const, self._server(start), self._server(end))
        tf = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
        info = mt5.symbol_info(self._sym(symbol))
        point = float(info.point) if info is not None else 0.0
        out = []
        for r in rates if rates is not None else []:
            ts = self._utc(int(r["time"]))
            if ts + tf > end:
                continue  # unfinished candle
            out.append(
                Candle(
                    ts,
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                    float(r["spread"]) * point,
                )
            )
        return out

    def market_status(self, symbol: str) -> MarketStatus:
        info = self._req().symbol_info(self._sym(symbol))
        if info is None:
            return MarketStatus(False, "symbol unavailable")
        if info.trade_mode == 0:
            return MarketStatus(False, "trading disabled for symbol")
        return fx_market_open(datetime.now(UTC))

    def estimate_margin(self, symbol: str, side: Side, volume: float, price: float) -> float | None:
        mt5 = self._req()
        kind = mt5.ORDER_TYPE_BUY if side is Side.BUY else mt5.ORDER_TYPE_SELL
        return mt5.order_calc_margin(kind, self._sym(symbol), volume, price)

    # --- trading -------------------------------------------------------------
    def submit_market_order(self, request: OrderRequest) -> OrderResult:
        mt5 = self._req()
        existing = self.find_order(request.client_order_id)
        if existing.found and existing.result is not None:
            return existing.result  # idempotency: never send twice
        sym = self._sym(request.symbol)
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            raise InstrumentUnavailable(f"no tick for {request.symbol}")
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": request.volume,
            "type": mt5.ORDER_TYPE_BUY if request.side is Side.BUY else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if request.side is Side.BUY else tick.bid,
            "deviation": self.settings.deviation_points,
            "magic": request.magic,
            "comment": request.client_order_id[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(sym),
        }
        if request.sl is not None:
            req["sl"] = request.sl
        if request.tp is not None:
            req["tp"] = request.tp
        try:
            res = mt5.order_send(req)
        except Exception as exc:  # noqa: BLE001 - terminal/IPC failure: outcome unknown
            raise UnknownOrderOutcome(f"order_send raised: {exc}") from exc
        if res is None:
            raise UnknownOrderOutcome(f"order_send returned None: {mt5.last_error()}")
        return self._map_result(request, res)

    def _map_result(self, request: OrderRequest, res: Any) -> OrderResult:
        rc = res.retcode
        if rc in (RETCODE_DONE, RETCODE_DONE_PARTIAL):
            pos_id = str(res.order)
            sl = None
            for p in self._req().positions_get(ticket=res.order) or []:
                pos_id, sl = str(p.ticket), (p.sl or None)
            status = (
                OrderStatus.FILLED
                if rc == RETCODE_DONE and res.volume >= request.volume - 1e-9
                else OrderStatus.PARTIALLY_FILLED
            )
            return OrderResult(
                request.client_order_id,
                status,
                str(res.order),
                pos_id,
                request.volume,
                res.volume,
                res.price,
                sl,
                datetime.now(UTC),
                None,
                0.0,
                False,
            )
        if rc in (RETCODE_TIMEOUT, RETCODE_CONNECTION, RETCODE_PLACED):
            raise UnknownOrderOutcome(f"retcode {rc}: {res.comment}")
        if rc == RETCODE_INVALID_VOLUME:
            raise InvalidVolume(res.comment)
        if rc == RETCODE_INVALID_STOPS:
            raise InvalidStop(res.comment)
        if rc == RETCODE_NO_MONEY:
            raise InsufficientMargin(res.comment)
        if rc == RETCODE_TOO_MANY_REQUESTS:
            raise BrokerRateLimited(res.comment)
        raise OrderRejected(f"retcode {rc}: {res.comment}")

    def modify_stop(self, position_id: str, sl: float, tp: float | None = None) -> StopModifyResult:
        mt5 = self._req()
        pos = mt5.positions_get(ticket=int(position_id))
        if not pos:
            raise PositionNotFound(position_id)
        p = pos[0]
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": p.ticket,
            "symbol": p.symbol,
            "sl": sl,
            "tp": tp if tp is not None else p.tp,
        }
        try:
            res = mt5.order_send(req)
        except Exception as exc:  # noqa: BLE001
            raise UnknownOrderOutcome(f"SLTP order_send raised: {exc}") from exc
        after = mt5.positions_get(ticket=p.ticket)
        confirmed = after[0].sl if after else p.sl
        ok = res is not None and res.retcode == RETCODE_DONE
        return StopModifyResult(
            position_id, ok, confirmed or None, None if ok else f"retcode {getattr(res, 'retcode', None)}"
        )

    def close_position(
        self, position_id: str, volume: float | None = None, client_order_id: str = ""
    ) -> OrderResult:
        mt5 = self._req()
        pos = mt5.positions_get(ticket=int(position_id))
        if not pos:
            raise PositionNotFound(position_id)
        p = pos[0]
        side = Side.SELL if p.type == mt5.POSITION_TYPE_BUY else Side.BUY
        req = OrderRequest(
            client_order_id or f"close{position_id}",
            p.symbol,
            side,
            volume or p.volume,
            None,
            None,
            p.magic,
            client_order_id,
        )
        tick = mt5.symbol_info_tick(p.symbol)
        r = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": req.volume,
            "position": p.ticket,
            "type": mt5.ORDER_TYPE_SELL if side is Side.SELL else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if side is Side.SELL else tick.ask,
            "deviation": self.settings.deviation_points,
            "magic": p.magic,
            "comment": (client_order_id or "close")[:31],
            "type_filling": self._filling(p.symbol),
        }
        try:
            res = mt5.order_send(r)
        except Exception as exc:  # noqa: BLE001
            raise UnknownOrderOutcome(f"close order_send raised: {exc}") from exc
        if res is None:
            raise UnknownOrderOutcome("close returned None")
        return self._map_result(req, res)

    def _to_position(self, p: Any) -> Position:
        return Position(
            str(p.ticket),
            p.symbol,
            Side.BUY if p.type == self._req().POSITION_TYPE_BUY else Side.SELL,
            p.volume,
            p.price_open,
            self._utc(p.time),
            p.sl or None,
            p.tp or None,
            p.magic,
            p.comment,
            p.profit,
            p.swap,
            0.0,
            False,
        )

    def list_positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        mt5 = self._req()
        raw = mt5.positions_get(symbol=self._sym(symbol)) if symbol else mt5.positions_get()
        out = [self._to_position(p) for p in raw or []]
        return [p for p in out if magic is None or p.magic == magic]

    def list_orders(self) -> list[OrderResult]:
        return []

    def order_history(self, since: datetime) -> list[OrderResult]:
        mt5 = self._req()
        out = []
        for o in (
            mt5.history_orders_get(
                self._server(since), self._server(datetime.now(UTC) + timedelta(minutes=1))
            )
            or []
        ):
            out.append(
                OrderResult(
                    o.comment,
                    OrderStatus.FILLED if o.volume_current == 0 else OrderStatus.PARTIALLY_FILLED,
                    str(o.ticket),
                    str(o.position_id),
                    o.volume_initial,
                    o.volume_initial - o.volume_current,
                    o.price_current,
                    o.sl or None,
                    self._utc(o.time_done),
                )
            )
        return out

    def deal_history(self, since: datetime, position_id: str | None = None) -> list[Deal]:
        mt5 = self._req()
        raw = (
            mt5.history_deals_get(position=int(position_id))
            if position_id
            else mt5.history_deals_get(
                self._server(since), self._server(datetime.now(UTC) + timedelta(minutes=1))
            )
        )
        reasons = {
            getattr(mt5, "DEAL_REASON_SL", 4): DealReason.STOP_LOSS,
            getattr(mt5, "DEAL_REASON_TP", 5): DealReason.TAKE_PROFIT,
            getattr(mt5, "DEAL_REASON_SO", 6): DealReason.STOP_OUT,
        }
        out = []
        for d in raw or []:
            entry = DealEntry.IN if d.entry == getattr(mt5, "DEAL_ENTRY_IN", 0) else DealEntry.OUT
            out.append(
                Deal(
                    str(d.ticket),
                    str(d.position_id),
                    str(d.order),
                    d.symbol,
                    Side.BUY if d.type == 0 else Side.SELL,
                    d.volume,
                    d.price,
                    self._utc(d.time),
                    entry,
                    reasons.get(d.reason, DealReason.CLIENT),
                    d.profit,
                    d.commission,
                    d.swap,
                    d.magic,
                    d.comment,
                    False,
                )
            )
        return [d for d in out if d.time >= since]

    def find_order(self, client_order_id: str) -> OrderLookup:
        mt5 = self._req()
        lookback = datetime.now(UTC) - timedelta(days=2)
        for o in (
            mt5.history_orders_get(
                self._server(lookback), self._server(datetime.now(UTC) + timedelta(minutes=1))
            )
            or []
        ):
            if o.comment == client_order_id[:31]:
                filled = o.volume_initial - o.volume_current
                status = (
                    OrderStatus.FILLED
                    if filled >= o.volume_initial - 1e-9 and filled > 0
                    else (OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.REJECTED)
                )
                return OrderLookup(
                    client_order_id,
                    True,
                    OrderResult(
                        client_order_id,
                        status,
                        str(o.ticket),
                        str(o.position_id),
                        o.volume_initial,
                        filled,
                        o.price_current,
                        o.sl or None,
                        self._utc(o.time_done),
                    ),
                )
        for p in mt5.positions_get() or []:
            if p.comment == client_order_id[:31]:
                return OrderLookup(
                    client_order_id,
                    True,
                    OrderResult(
                        client_order_id,
                        OrderStatus.FILLED,
                        None,
                        str(p.ticket),
                        p.volume,
                        p.volume,
                        p.price_open,
                        p.sl or None,
                        self._utc(p.time),
                    ),
                )
        return OrderLookup(client_order_id, False)
