from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.base import session_scope
from app.models import Bot, Job, PositionLeg, TradingCycle
from app.services import risk_service
from app.services.trading_runtime import run_tick
from tests.conftest import VIEWER, Api
from trading.interfaces.types import Environment
from trading.risk.circuit_breakers import BreakerScope, BreakerType


# --- auth ------------------------------------------------------------------------
def test_health_is_public(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert {c["name"] for c in r.json()["checks"]} >= {"database", "worker"}


def test_protected_endpoints_require_login(client):
    assert client.get("/api/v1/bots").status_code == 401
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_login_logout_and_me(client):
    a = Api(client)
    data = a.login()
    assert data["user"]["role"] == "admin"
    assert "fx_session" in client.cookies
    assert a.get("/auth/me").json()["user"]["username"] == "admin"
    assert a.post("/auth/logout").status_code == 200
    assert a.get("/auth/me").status_code == 401


def test_bad_password_and_lockout(client):
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
        assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "correct-horse-battery"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "too_many_attempts"


def test_csrf_required_for_mutations(api):
    r = api.c.post("/api/v1/bots/news-volatility/actions/enable")  # no header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_viewer_is_read_only(client):
    a = Api(client)
    a.login(VIEWER)
    assert a.get("/bots").status_code == 200
    assert a.post("/bots/news-volatility/actions/enable").status_code == 403
    assert a.put("/risk/config", json={"config": {}}).status_code == 403


# --- bots ----------------------------------------------------------------------------
def test_bots_listed_disabled_by_default(api):
    bots = api.get("/bots").json()
    assert {b["id"] for b in bots} == {"news-volatility", "daily-trading"}
    assert all(b["status"] == "disabled" and not b["enabled"] for b in bots)
    assert all(b["allowed_actions"] == ["enable"] for b in bots)


def test_bot_lifecycle_through_worker(api):
    assert api.post("/bots/news-volatility/actions/start").status_code == 409  # disabled -> start invalid
    assert api.post("/bots/news-volatility/actions/enable").json()["status"] == "stopped"
    b = api.post("/bots/news-volatility/actions/start").json()
    assert b["status"] == "starting"  # not running until the worker's pre-start checks pass
    run_tick(Environment.SIMULATION)
    b = api.get("/bots/news-volatility").json()
    assert b["status"] == "running", b["last_error"]
    assert api.post("/bots/news-volatility/actions/pause").json()["status"] == "paused"
    assert api.post("/bots/news-volatility/actions/resume").json()["status"] == "running"
    assert api.post("/bots/news-volatility/actions/stop").json()["status"] == "stopped"
    logs = api.get("/logs", params={"category": "bot_lifecycle"}).json()
    assert logs["total"] >= 5


def test_bot_config_validation_and_versioning(api):
    cur = api.get("/bots/daily-trading/config").json()
    assert cur["version"] == 1 and "effective" in cur
    bad = dict(cur["config"])
    bad["params"] = {**bad["params"], "ema_fast": 300, "ema_slow": 200}
    r = api.put("/bots/daily-trading/config", json={"config": bad})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"
    good = dict(cur["config"])
    good["max_trades_per_day"] = 2
    r = api.put("/bots/daily-trading/config", json={"config": good})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert len(api.get("/bots/daily-trading/config/history").json()) == 2


# --- risk ------------------------------------------------------------------------------
def test_risk_increase_requires_confirmation(api):
    cfg = api.get("/risk/config").json()
    assert cfg["fields"] and all("unit" in f and "description" in f for f in cfg["fields"])
    c = cfg["config"]
    c["global"]["max_daily_loss_pct"] = 2.0
    r = api.put("/risk/config", json={"config": c})
    assert r.status_code == 409
    assert r.json()["error"]["details"]["required_confirm_text"] == "CONFIRM RISK INCREASE"
    r = api.put("/risk/config", json={"config": c, "confirm_text": "CONFIRM RISK INCREASE"})
    assert r.status_code == 200 and "global.max_daily_loss_pct" in r.json()["loosened"]
    # reductions need no confirmation
    c["global"]["max_daily_loss_pct"] = 1.0
    assert api.put("/risk/config", json={"config": c}).status_code == 200


def test_risk_config_rejects_hard_limit_violations(api):
    c = api.get("/risk/config").json()["config"]
    c["news_volatility"]["max_cycle_risk_pct"] = 10
    r = api.put("/risk/config", json={"config": c, "confirm_text": "CONFIRM RISK INCREASE"})
    assert r.status_code == 400


# --- paper trading flow ----------------------------------------------------------------
def _enable(api, bot):
    api.post(f"/bots/{bot}/actions/enable")


def test_manual_dual_entry_cycle_and_liquidation(loose_spreads):
    api = loose_spreads
    _enable(api, "news-volatility")
    r = api.post(
        "/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15}
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["approved"], res
    cid = res["cycle_id"]
    detail = api.get(f"/cycles/{cid}").json()
    assert detail["state"] == "both_legs_open"
    assert detail["simulated"] is True
    assert {lg["side"] for lg in detail["legs"]} == {"BUY", "SELL"}
    assert all(lg["confirmed_sl"] for lg in detail["legs"])
    assert detail["risk_decision"]["approved"] and detail["risk_decision"]["planned_risk"] > 0
    pos = api.get("/positions").json()
    assert len(pos) == 2 and all(p["simulated"] for p in pos)
    # duplicate concurrent cycle on same instrument is blocked by risk
    r2 = api.post(
        "/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15}
    ).json()
    assert not r2["approved"] and any("cycles_per_instrument" in x for x in r2["reasons"])
    # liquidation needs the phrase
    assert api.post(f"/cycles/{cid}/close", json={}).status_code == 409
    r = api.post(f"/cycles/{cid}/close", json={"confirm_text": "LIQUIDATE"})
    assert r.status_code == 200 and r.json()["state"] == "completed"
    assert api.get("/positions").json() == []
    trades = api.get("/trades", params={"cycle_id": cid}).json()
    assert trades["total"] == 2
    csv = api.get("/trades/export.csv", params={"cycle_id": cid})
    assert csv.status_code == 200 and csv.text.count("\n") == 3


def test_daily_manual_entry_and_trade_filters(loose_spreads):
    api = loose_spreads
    _enable(api, "daily-trading")
    r = api.post(
        "/paper/manual-entry",
        json={
            "bot_id": "daily-trading",
            "symbol": "GBPUSD",
            "side": "SELL",
            "stop_pips": 20,
            "target_pips": 30,
        },
    ).json()
    assert r["approved"], r
    c = api.get(f"/cycles/{r['cycle_id']}").json()
    assert c["kind"] == "single" and c["state"] == "open"
    leg = c["legs"][0]
    assert leg["side"] == "SELL" and leg["tp"] is not None and leg["confirmed_sl"] > leg["fill_price"]
    assert api.get("/trades", params={"side": "BUY"}).json()["total"] == 0
    assert api.get("/trades", params={"side": "SELL", "outcome": "open"}).json()["total"] == 1
    page = api.get("/trades", params={"page_size": 1, "page": 1}).json()
    assert page["page_size"] == 1 and len(page["items"]) == 1


def test_active_breaker_blocks_entries(loose_spreads):
    api = loose_spreads
    _enable(api, "news-volatility")
    with session_scope() as s:
        risk_service.trip_breaker(
            s, Environment.SIMULATION, BreakerType.DAILY_LOSS_LIMIT, BreakerScope.GLOBAL, None, "test halt"
        )
    r = api.post(
        "/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15}
    ).json()
    assert not r["approved"] and any("circuit_breakers" in x for x in r["reasons"])
    breakers = api.get("/risk/breakers", params={"active": True}).json()["items"]
    assert breakers and breakers[0]["critical"]
    bid = breakers[0]["id"]
    assert api.post(f"/risk/breakers/{bid}/clear", json={"note": "reviewed"}).status_code == 409
    r = api.post(f"/risk/breakers/{bid}/clear", json={"confirm_text": "CLEAR", "note": "reviewed in test"})
    assert r.status_code == 200 and not r.json()["active"]
    assert api.post(
        "/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15}
    ).json()["approved"]


def test_disabled_bot_cannot_trade(api):
    r = api.post(
        "/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15}
    )
    assert r.status_code == 409


def test_shutdown_preview_and_liquidation(loose_spreads):
    api = loose_spreads
    _enable(api, "news-volatility")
    cid = api.post(
        "/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "USDJPY", "stop_pips": 20}
    ).json()["cycle_id"]
    prev = api.get("/bots/news-volatility/shutdown-preview").json()
    assert len(prev["cycles"]) == 1 and len(prev["positions"]) == 2
    assert api.post("/bots/news-volatility/shutdown", json={"liquidate": True}).status_code == 409
    r = api.post("/bots/news-volatility/shutdown", json={"liquidate": True, "confirm_text": "LIQUIDATE"})
    assert r.status_code == 200 and r.json()["closed_cycles"] == [cid]


def test_worker_tick_manages_and_snapshots(loose_spreads):
    api = loose_spreads
    _enable(api, "news-volatility")
    api.post("/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15})
    out = run_tick(Environment.SIMULATION)
    assert out["managed_cycles"] == 1
    summ = api.get("/dashboard/summary").json()
    assert summ["account"]["simulated"] is True
    assert summ["active_cycles"] == 1 and summ["open_positions"] == 2
    assert "win_rate" in summ["performance"]["insufficient"]
    charts = api.get("/dashboard/charts").json()
    assert len(charts["equity"]) >= 1


def test_paper_reset_refused_with_active_cycles(loose_spreads):
    api = loose_spreads
    _enable(api, "news-volatility")
    api.post("/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15})
    assert (
        api.post(
            "/paper/reset", json={"initial_balance": 5000, "confirm_text": "RESET PAPER ACCOUNT"}
        ).status_code
        == 409
    )


def test_paper_reset(api):
    assert api.post("/paper/reset", json={"initial_balance": 5000}).status_code == 409
    r = api.post("/paper/reset", json={"initial_balance": 5000, "confirm_text": "RESET PAPER ACCOUNT"})
    assert r.status_code == 200
    assert api.get("/paper/account").json()["account"]["balance"] == 5000


# --- environment ---------------------------------------------------------------------
def test_live_mode_refused_without_broker(api):
    r = api.post("/system/environment", json={"target": "live"})
    assert r.status_code == 409
    checks = {c["name"]: c["passed"] for c in r.json()["error"]["details"]["checks"]}
    assert checks["broker_integration"] is False
    assert api.get("/system/status").json()["environment"] == "simulation"


def test_switch_to_paper_requires_stopped_bots(api):
    _enable(api, "daily-trading")
    api.post("/bots/daily-trading/actions/start")
    assert api.post("/system/environment", json={"target": "paper"}).status_code == 409
    api.post("/bots/daily-trading/actions/stop")
    r = api.post("/system/environment", json={"target": "paper"})
    assert r.status_code == 200 and r.json()["environment"] == "paper"


# --- integrations --------------------------------------------------------------------------
def test_integration_secrets_never_returned(api):
    r = api.put(
        "/integrations/anthropic",
        json={"enabled": True, "settings": {}, "secrets": {"api_key": "sk-ant-secret-value-123"}},
    )
    assert r.status_code == 200
    assert "sk-ant-secret-value-123" not in r.text
    integ = next(i for i in api.get("/integrations").json() if i["key"] == "anthropic")
    f = next(f for f in integ["fields"] if f["name"] == "api_key")
    assert f["value"] is None and f["is_set"] is True
    assert integ["status"] == "unverified"  # configured but never tested -> not "connected"
    logs = api.get("/logs", params={"category": "integration"}).json()
    assert "sk-ant-secret-value-123" not in str(logs)


def test_ai_unavailable_when_not_configured(api):
    r = api.post("/ai/analyze", json={"subject": "risk", "id": ""})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_configured"


def test_mt5_test_reports_unavailable(api):
    api.put(
        "/integrations/mt5",
        json={"enabled": True, "settings": {"login": "123", "server": "Demo"}, "secrets": {"password": "pw"}},
    )
    r = api.post("/integrations/mt5/test").json()
    assert r["status"] in ("unavailable", "disconnected")


# --- backtests -----------------------------------------------------------------------------
def test_backtest_create_execute_and_results(api):
    body = {
        "bot_kind": "news_volatility",
        "symbol": "EURUSD",
        "start": "2025-01-06T00:00:00Z",
        "end": "2025-01-20T00:00:00Z",
        "strategy": {"entry_mode": {"news": False, "volatility": True}},
    }
    r = api.post("/backtests", json=body)
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    assert r.json()["status"] == "queued" and r.json()["data_source"] == "synthetic"
    with session_scope() as s:
        assert s.scalars(select(Job).where(Job.type == "backtest")).first() is not None
    from app.services.backtest_service import execute_run

    execute_run(run_id)
    d = api.get(f"/backtests/{run_id}").json()
    assert d["status"] == "succeeded"
    assert any("SYNTHETIC" in w for w in d["warnings"])
    assert d["reproducibility"]["data_fingerprint"]
    trades = api.get(f"/backtests/{run_id}/trades").json()
    assert trades["total"] == d["metrics"]["trade_count"]
    assert api.get(f"/backtests/{run_id}/export.csv").status_code == 200
    # Reproducible: same config -> same result
    run2 = api.post("/backtests", json=body).json()["id"]
    execute_run(run2)
    assert api.get(f"/backtests/{run2}").json()["metrics"]["net_pnl"] == d["metrics"]["net_pnl"]


def test_backtest_invalid_config(api):
    r = api.post(
        "/backtests",
        json={
            "bot_kind": "daily_trading",
            "symbol": "EURUSD",
            "start": "2025-02-01T00:00:00Z",
            "end": "2025-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 400


def test_dataset_import_quality_report(api):
    rows = ["time,open,high,low,close,spread"]
    t = datetime(2025, 1, 6, tzinfo=UTC)
    for i in range(100):
        ts = (t + timedelta(minutes=i)).isoformat()
        rows.append(f"{ts},1.10000,1.10010,1.09990,1.10005,0.00008")
    rows.append(rows[-1])  # duplicate timestamp
    r = api.c.post(
        "/api/v1/datasets/import",
        files={"file": ("d.csv", "\n".join(rows))},
        data={"symbol": "EURUSD", "timeframe": "M1"},
        headers={"X-CSRF-Token": api.csrf},
    )
    assert r.status_code == 200
    assert r.json()["imported"] is False
    assert any(i["code"] == "duplicates" for i in r.json()["quality"]["issues"])
    r = api.c.post(
        "/api/v1/datasets/import",
        files={"file": ("d.csv", "\n".join(rows[:-1]))},
        data={"symbol": "EURUSD", "timeframe": "M1"},
        headers={"X-CSRF-Token": api.csrf},
    )
    assert r.json()["imported"] is True


# --- notifications --------------------------------------------------------------------------
def test_notifications_and_read(loose_spreads):
    api = loose_spreads
    _enable(api, "news-volatility")
    api.post("/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15})
    n = api.get("/notifications", params={"unread": True}).json()
    assert n["total"] >= 1
    assert api.post("/notifications/read-all").json()["updated"] >= 1
    assert api.get("/notifications", params={"unread": True}).json()["total"] == 0


def test_pagination_bounds(api):
    assert api.get("/logs", params={"page_size": 1000}).status_code == 422
    assert api.get("/logs", params={"page": 0}).status_code == 422


def test_bot_positions_isolated_per_bot(loose_spreads):
    api = loose_spreads
    _enable(api, "news-volatility")
    _enable(api, "daily-trading")
    api.post("/paper/manual-entry", json={"bot_id": "news-volatility", "symbol": "EURUSD", "stop_pips": 15})
    api.post(
        "/paper/manual-entry",
        json={"bot_id": "daily-trading", "symbol": "GBPUSD", "side": "BUY", "stop_pips": 20},
    )
    with session_scope() as s:
        magic = {c.bot_id: c.magic for c in s.scalars(select(TradingCycle))}
        assert magic["news-volatility"] != magic["daily-trading"]
        legs = s.scalars(select(PositionLeg)).all()
        assert len({lg.client_order_id for lg in legs}) == len(legs)
        assert {b.id for b in s.scalars(select(Bot))} == {"news-volatility", "daily-trading"}


def test_failed_backtest_is_marked_failed_not_stuck(api, monkeypatch):
    from app.services import backtest_service

    body = {
        "bot_kind": "daily_trading",
        "symbol": "EURUSD",
        "start": "2025-01-06T00:00:00Z",
        "end": "2025-01-13T00:00:00Z",
    }
    run_id = api.post("/backtests", json=body).json()["id"]
    monkeypatch.setattr(backtest_service, "_events", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    backtest_service.execute_run(run_id)
    d = api.get(f"/backtests/{run_id}").json()
    assert d["status"] == "failed" and "boom" in d["error"]


def test_backtests_filter_by_bot_kind(api):
    for kind in ("daily_trading", "news_volatility"):
        api.post(
            "/backtests",
            json={
                "bot_kind": kind,
                "symbol": "EURUSD",
                "start": "2025-01-06T00:00:00Z",
                "end": "2025-01-08T00:00:00Z",
            },
        )
    only = api.get("/backtests", params={"bot_kind": "daily_trading"}).json()
    assert only["total"] == 1 and only["items"][0]["bot_kind"] == "daily_trading"


def test_failed_commit_is_reported_not_silently_lost(api, monkeypatch):
    """The DB commit must finish before the response is sent: a failing commit returns an error, never 200."""
    from sqlalchemy.orm import Session

    def boom(self):  # noqa: ANN001
        raise RuntimeError("commit failed")

    from fastapi.testclient import TestClient

    from app.main import app

    raw = TestClient(app, raise_server_exceptions=False)  # return the real HTTP response
    raw.cookies.update(api.c.cookies)
    monkeypatch.setattr(Session, "commit", boom)
    try:
        r = raw.put(
            "/api/v1/settings", json={"timezone": "Africa/Nairobi"}, headers={"X-CSRF-Token": api.csrf}
        )
    finally:
        monkeypatch.undo()
    assert r.status_code == 500
    assert api.get("/settings").json()["timezone"] == "UTC"  # nothing was stored


def test_broker_refusing_orders_trips_one_breaker_and_blocks_start(api, monkeypatch):
    """Algo Trading off: one breaker (not one per tick), entries blocked, clears when back on."""
    from app.models import CircuitBreakerRow, Notification
    from trading.adapters.simulated_broker import SimulatedBroker

    monkeypatch.setattr(SimulatedBroker, "trading_allowed", lambda self: False)
    api.post("/bots/daily-trading/actions/enable")
    api.post("/bots/daily-trading/actions/start")
    for _ in range(3):
        run_tick(Environment.SIMULATION)
    with session_scope() as s:
        rows = s.scalars(
            select(CircuitBreakerRow).where(CircuitBreakerRow.type == "broker_disconnected")
        ).all()
        notes = s.scalars(select(Notification).where(Notification.kind == "broker_disconnected")).all()
        assert len(rows) == 1 and rows[0].active and "Algo Trading" in rows[0].reason
        assert len(notes) == 1
    bot = api.get("/bots/daily-trading").json()
    assert bot["status"] == "error" and "Algo Trading" in bot["last_error"]
    monkeypatch.setattr(SimulatedBroker, "trading_allowed", lambda self: True)
    run_tick(Environment.SIMULATION)
    with session_scope() as s:
        assert not s.scalars(select(CircuitBreakerRow).where(CircuitBreakerRow.active.is_(True))).all()


def test_implausible_broker_reading_never_trips_loss_halts(api, monkeypatch):
    """A momentary equity-0 reading (e.g. terminal mid-login) must not be recorded or trigger halts."""
    import dataclasses

    from app.models import AccountSnapshot, CircuitBreakerRow
    from trading.adapters.simulated_broker import SimulatedBroker

    run_tick(Environment.SIMULATION)  # one good snapshot first
    real = SimulatedBroker.account_info

    def empty(self):
        return dataclasses.replace(real(self), balance=0.0, equity=0.0, is_simulated=False)

    monkeypatch.setattr(SimulatedBroker, "account_info", empty)
    for _ in range(3):
        run_tick(Environment.SIMULATION)
    with session_scope() as s:
        types = {
            b.type for b in s.scalars(select(CircuitBreakerRow).where(CircuitBreakerRow.active.is_(True)))
        }
        assert types == {"broker_disconnected"}  # no daily_loss_limit / max_drawdown
        assert not s.scalars(select(AccountSnapshot).where(AccountSnapshot.equity <= 0)).all()
    monkeypatch.setattr(SimulatedBroker, "account_info", real)
    run_tick(Environment.SIMULATION)
    with session_scope() as s:
        assert not s.scalars(select(CircuitBreakerRow).where(CircuitBreakerRow.active.is_(True))).all()


def test_environment_caps_are_editable_and_loosening_them_needs_confirmation(api):
    data = api.get("/risk/config").json()
    paths = {f["path"]: f for f in data["fields"]}
    live = paths["environments.live.max_cycle_risk_pct"]
    assert live["nullable"] and live["max"] == 2.0 and live["default"] == 0.5
    assert paths["environments.paper.max_cycle_risk_pct"]["default"] is None  # no extra cap
    cfg = data["config"]
    cfg["environments"]["live"]["max_cycle_risk_pct"] = 1.0
    assert api.put("/risk/config", json={"config": cfg}).status_code == 409
    r = api.put("/risk/config", json={"config": cfg, "confirm_text": "CONFIRM RISK INCREASE"})
    assert r.status_code == 200
    assert api.get("/risk/config").json()["config"]["environments"]["live"]["max_cycle_risk_pct"] == 1.0


def test_entry_survives_a_slow_database(loose_spreads, monkeypatch):
    """Remote DBs add seconds between the decision and the risk check; the price must still count as fresh."""
    import time as _time

    from app.services.execution_store import DbExecutionStore

    real_save = DbExecutionStore.save

    def slow_save(self, cycle):  # noqa: ANN001
        _time.sleep(1.5)
        return real_save(self, cycle)

    api = loose_spreads
    api.post("/bots/daily-trading/actions/enable")
    monkeypatch.setattr(DbExecutionStore, "save", slow_save)
    r = api.post(
        "/paper/manual-entry",
        json={"bot_id": "daily-trading", "symbol": "EURUSD", "side": "BUY", "stop_pips": 20},
    ).json()
    assert r["approved"], r["reasons"]


def test_manual_entry_in_live_is_demo_only(loose_spreads, monkeypatch):
    """Live mode: manual test entries go through only when the broker reports a DEMO account."""
    import dataclasses

    from app.services import trading_runtime
    from app.services.broker_service import build_broker as real_build
    from app.services.system_settings import set_setting
    from trading.adapters.simulated_broker import SimulatedBroker

    api = loose_spreads
    api.post("/bots/daily-trading/actions/enable")
    with session_scope() as s:
        set_setting(s, "environment", "live")
    # Stand in a simulated broker for MT5 (tests never touch a real terminal).
    monkeypatch.setattr(
        trading_runtime,
        "build_broker",
        lambda s, env, reg, clock: real_build(s, Environment.SIMULATION, reg, clock),
    )
    body = {"bot_id": "daily-trading", "symbol": "EURUSD", "side": "BUY", "stop_pips": 20}

    real_info = SimulatedBroker.account_info
    monkeypatch.setattr(
        SimulatedBroker, "account_info", lambda self: dataclasses.replace(real_info(self), is_demo=False)
    )
    r = api.post("/paper/manual-entry", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_allowed_on_real_account"
    with session_scope() as s:
        assert not s.scalars(select(TradingCycle)).all()  # nothing was created, let alone sent

    monkeypatch.setattr(SimulatedBroker, "account_info", real_info)  # simulated account reports demo
    r = api.post("/paper/manual-entry", json=body)
    assert r.status_code == 200 and r.json()["approved"], r.json()


def test_data_blips_block_at_once_but_notify_only_if_they_last(api):
    from datetime import timedelta

    from app.db.base import utcnow
    from app.models import CircuitBreakerRow, Notification

    with session_scope() as s:
        row = risk_service.trip_breaker(
            s,
            Environment.SIMULATION,
            BreakerType.STALE_MARKET_DATA,
            BreakerScope.INSTRUMENT,
            "GBPUSD",
            "quote is stale (6s)",
        )
        assert row.active  # entries on GBPUSD are blocked immediately
        assert risk_service.notify_lingering_blips(s, Environment.SIMULATION, utcnow()) == 0
        assert not s.scalars(select(Notification)).all()  # a 6-second blip is not an alert
        row.activated_at = utcnow() - timedelta(seconds=90)
        s.flush()
        assert risk_service.notify_lingering_blips(s, Environment.SIMULATION, utcnow()) == 1
        assert risk_service.notify_lingering_blips(s, Environment.SIMULATION, utcnow()) == 0  # only once
        notes = s.scalars(select(Notification)).all()
        assert len(notes) == 1 and "GBPUSD for 90s" in notes[0].message
        # a real fault still alerts immediately
        risk_service.trip_breaker(
            s, Environment.SIMULATION, BreakerType.STOP_PLACEMENT_FAILURE, BreakerScope.GLOBAL, None, "x"
        )
        assert len(s.scalars(select(Notification)).all()) == 2
        assert (
            s.scalars(select(CircuitBreakerRow).where(CircuitBreakerRow.type == "stop_placement_failure"))
            .first()
            .critical
        )
