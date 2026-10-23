"""MT5 adapter against a fake ``MetaTrader5`` module (no terminal required)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest

from trading.adapters.base import InsufficientMargin, OrderRequest, OrderStatus, UnknownOrderOutcome
from trading.adapters.mt5.adapter import MT5Adapter, MT5Settings
from trading.interfaces.types import MarginMode, Side


class FakeMT5:
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    POSITION_TYPE_BUY = 0

    def __init__(self, margin_mode=2, server_offset_h=0.0, filling_flags=2, logged_in_as=123, algo_on=True):
        self.margin_mode = margin_mode
        self.logged_in_as = logged_in_as  # account the terminal is currently on
        self.algo_on = algo_on
        self.init_calls: list[dict] = []
        self.server_offset = server_offset_h * 3600
        self.filling_flags = filling_flags
        self.sent: list[dict] = []
        self.next_result = None
        self.raise_on_send = False
        self.history: list = []
        self.positions: list = []

    def initialize(self, **kw):
        self.init_calls.append(kw)  # like the real module: never switches account once attached
        return True

    def login(self, login, password=None, server=None):
        self.init_calls.append({"login": login, "server": server})
        if password == "pw":
            self.logged_in_as = login
            return True
        return False

    def terminal_info(self):
        return NS(connected=True, trade_allowed=self.algo_on)

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return NS(
            login=self.logged_in_as,
            currency="USD",
            balance=1000.0,
            equity=1000.0,
            margin=0.0,
            margin_free=1000.0,
            margin_level=0.0,
            leverage=30,
            margin_mode=self.margin_mode,
            trade_mode=0,
            server="Demo",
        )

    def symbol_info_tick(self, sym):
        now = time.time() + self.server_offset  # MT5 stamps ticks in server time
        return NS(bid=1.1, ask=1.1001, time=int(now), time_msc=int(now * 1000))

    def symbol_select(self, name, enable):
        return True

    def symbol_info(self, name):
        return NS(name=name, point=0.00001, filling_mode=self.filling_flags, trade_mode=4, visible=True)

    def history_orders_get(self, a, b):
        return self.history

    def positions_get(self, **kw):
        return self.positions

    def order_send(self, req):
        self.sent.append(req)
        if self.raise_on_send:
            raise RuntimeError("IPC timeout")
        return self.next_result


def adapter(fake):
    a = MT5Adapter(MT5Settings(login=123, server="Demo", password_secret="pw"), mt5_module=fake)
    a.connect()
    return a


def req(cid="nvabcB"):
    return OrderRequest(cid, "EURUSD", Side.BUY, 0.1, 1.098, magic=1001)


def test_margin_mode_detection():
    assert adapter(FakeMT5(2)).account_info().margin_mode is MarginMode.HEDGING
    a = adapter(FakeMT5(0))
    assert a.account_info().margin_mode is MarginMode.NETTING and not a.capabilities().hedging


def test_successful_fill_carries_magic_and_client_id():
    f = FakeMT5()
    f.next_result = NS(retcode=10009, order=55, volume=0.1, price=1.1001, comment="done")
    res = adapter(f).submit_market_order(req())
    assert res.status is OrderStatus.FILLED and res.fill_price == 1.1001
    assert f.sent[0]["magic"] == 1001 and f.sent[0]["comment"] == "nvabcB" and f.sent[0]["sl"] == 1.098


def test_exception_or_timeout_is_unknown_outcome():
    f = FakeMT5()
    f.raise_on_send = True
    with pytest.raises(UnknownOrderOutcome):
        adapter(f).submit_market_order(req())
    f.raise_on_send = False
    f.next_result = NS(retcode=10012, comment="timeout")
    with pytest.raises(UnknownOrderOutcome):
        adapter(f).submit_market_order(req("other"))


def test_existing_order_is_not_sent_twice():
    f = FakeMT5()
    f.history = [
        NS(
            comment="nvabcB",
            volume_initial=0.1,
            volume_current=0.0,
            ticket=7,
            position_id=70,
            price_current=1.1,
            sl=1.098,
            time_done=1_700_000_000,
        )
    ]
    res = adapter(f).submit_market_order(req())
    assert res.status is OrderStatus.FILLED and f.sent == []


def test_no_money_maps_to_insufficient_margin():
    f = FakeMT5()
    f.next_result = NS(retcode=10019, comment="no money")
    with pytest.raises(InsufficientMargin):
        adapter(f).submit_market_order(req())


def test_server_time_offset_is_detected_and_removed():
    a = adapter(FakeMT5(server_offset_h=3))  # broker server on UTC+3
    assert a.offset_verified and a._offset_hours() == 3
    q = a.get_quote("EURUSD")
    assert abs((datetime.now(UTC) - q.time).total_seconds()) < 5  # quote time is real UTC, not +3h


def test_filling_mode_follows_symbol_flags():
    f = FakeMT5(filling_flags=1)  # FOK only
    f.next_result = NS(retcode=10009, order=55, volume=0.1, price=1.1001, comment="done")
    adapter(f).submit_market_order(req())
    assert f.sent[0]["type_filling"] == FakeMT5.ORDER_FILLING_FOK
    f2 = FakeMT5(filling_flags=0)  # neither -> RETURN
    f2.next_result = NS(retcode=10009, order=56, volume=0.1, price=1.1001, comment="done")
    adapter(f2).submit_market_order(req("x2"))
    assert f2.sent[0]["type_filling"] == FakeMT5.ORDER_FILLING_RETURN


def test_no_relogin_when_terminal_already_on_the_account():
    f = FakeMT5(logged_in_as=123)
    adapter(f)
    assert all("login" not in c for c in f.init_calls)  # re-login would switch Algo Trading off in MT5


def test_logs_in_when_terminal_is_on_another_account():
    f = FakeMT5(logged_in_as=999)
    a = adapter(f)
    assert any(c.get("login") == 123 for c in f.init_calls)
    assert a.check_connection().connected


def test_trading_allowed_reflects_algo_trading_button():
    assert adapter(FakeMT5(algo_on=True)).trading_allowed()
    assert not adapter(FakeMT5(algo_on=False)).trading_allowed()


def test_wrong_password_fails_instead_of_pretending():
    f = FakeMT5(logged_in_as=999)
    a = MT5Adapter(MT5Settings(login=123, server="Demo", password_secret="wrong"), mt5_module=f)
    st = a.connect()
    assert not st.connected and "login failed" in st.detail
    assert f.logged_in_as == 999


def test_small_broker_clock_drift_is_corrected_large_is_not():
    a = adapter(FakeMT5(server_offset_h=3 + 6 / 3600))  # broker 3h ahead plus 6s of clock drift
    q = a.get_quote("EURUSD")
    age = (datetime.now(UTC) - q.time).total_seconds()
    assert -1 < age < 2 and 5 < a.clock_skew < 7
    b = adapter(FakeMT5(server_offset_h=3 + 120 / 3600))  # 2 minutes off: a real clock problem
    age_b = (datetime.now(UTC) - b.get_quote("EURUSD").time).total_seconds()
    assert age_b < -100  # left uncorrected, so freshness checks reject it


def test_clock_skew_estimate_follows_a_later_clock_sync():
    f = FakeMT5(server_offset_h=3 + 6 / 3600)
    a = adapter(f)
    a.get_quote("EURUSD")
    assert a.clock_skew > 5
    f.server_offset = 3 * 3600  # PC clock now synced: no more drift
    for _ in range(40):
        a.get_quote("EURUSD")
    assert a.clock_skew < 1
    assert abs((datetime.now(UTC) - a.get_quote("EURUSD").time).total_seconds()) < 2
