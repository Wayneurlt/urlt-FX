"""Environment switching and the live-trading readiness gate."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError, confirm_required
from app.db.base import session_scope
from app.models import Bot, IntegrationConfig
from app.services import risk_service
from app.services.audit import audit, notify
from app.services.bots_service import current_config
from app.services.execution_store import load_active_cycles
from app.services.instruments import upsert_broker_spec
from app.services.integrations_service import build_mt5_adapter
from app.services.system_settings import current_environment, set_setting
from trading.adapters.base import BrokerError
from trading.interfaces.types import BotKind, Environment, MarginMode


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def live_readiness(s: Session, fetch_specs: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    row = s.get(IntegrationConfig, "mt5")
    configured = bool(row and row.enabled and row.status == "connected")
    checks.append(
        _check(
            "broker_integration",
            configured,
            "MT5 integration enabled and last connection test succeeded"
            if configured
            else "MT5 integration is not enabled/verified (Integrations -> MetaTrader 5 -> Test)",
        )
    )
    account_id = None
    adapter = build_mt5_adapter(s) if configured else None
    if adapter is None:
        for name in (
            "account_validation",
            "instrument_specs",
            "account_mode",
            "reconciliation",
            "stop_management",
        ):
            checks.append(_check(name, False, "requires a connected broker"))
    else:
        try:
            st = adapter.connect()
            if not st.connected:
                raise BrokerError(st.detail)
            acct = adapter.account_info()
            account_id = acct.account_id
            checks.append(
                _check(
                    "account_validation",
                    True,
                    f"account {acct.account_id} on {acct.server} ({'demo' if acct.is_demo else 'REAL MONEY'}), {acct.currency}, equity {acct.equity:.2f}",
                )
            )
            bots = list(s.scalars(select(Bot).where(Bot.enabled.is_(True))))
            symbols = sorted({sym for b in bots for sym in current_config(s, b).instruments})
            bad = []
            for sym in symbols:
                try:
                    spec = adapter.instrument_spec(sym)
                    if fetch_specs:
                        upsert_broker_spec(s, spec)
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"{sym}: {exc}")
            checks.append(
                _check(
                    "instrument_specs",
                    not bad and bool(symbols),
                    f"verified broker specs for {', '.join(symbols)}"
                    if not bad and symbols
                    else (
                        "; ".join(bad)
                        or "no bot is enabled - enable at least one bot (Trading Bots -> Enable) so its instruments can be verified with the broker"
                    ),
                )
            )
            needs_hedging = any(b.kind == BotKind.NEWS_VOLATILITY.value for b in bots)
            mode_ok = acct.margin_mode is MarginMode.HEDGING or not needs_hedging
            checks.append(
                _check(
                    "account_mode",
                    mode_ok,
                    f"margin mode {acct.margin_mode.value}"
                    + ("" if mode_ok else " - the dual-entry bot needs a hedging account"),
                )
            )
            adapter.list_positions()
            from datetime import timedelta

            from app.db.base import utcnow

            adapter.order_history(utcnow() - timedelta(days=1))
            checks.append(_check("reconciliation", True, "positions and order history readable"))
            caps = adapter.capabilities()
            checks.append(
                _check(
                    "stop_management",
                    caps.broker_side_stops,
                    "broker-side protective stops supported"
                    if caps.broker_side_stops
                    else "broker-side stops unsupported",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("account_validation", False, f"broker check failed: {type(exc).__name__}"))
        finally:
            try:
                adapter.disconnect()
            except Exception:  # noqa: BLE001
                pass
    try:
        risk_service.current_risk_config(s)
        checks.append(_check("risk_configuration", True, "risk configuration valid"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("risk_configuration", False, f"invalid risk configuration: {exc}"))
    env = current_environment(s)
    critical = [b for b in risk_service.active_breaker_rows(s, Environment.LIVE) if b.critical] + [
        b for b in risk_service.active_breaker_rows(s, env) if b.critical and env is not Environment.LIVE
    ]
    checks.append(
        _check(
            "no_critical_breakers",
            not critical,
            "none active" if not critical else "; ".join(f"{b.type}: {b.reason}" for b in critical),
        )
    )
    return {"ready": all(c["passed"] for c in checks), "checks": checks, "account_id": account_id}


def switch_environment(s: Session, target: str, actor: str, confirm_text: str | None) -> dict[str, Any]:
    try:
        tgt = Environment(target)
    except ValueError as exc:
        raise APIError(400, "invalid_environment", "target must be simulation, paper or live") from exc
    cur = current_environment(s)
    if tgt is cur:
        return {"environment": cur.value, "checks": []}
    running = [
        b.name
        for b in s.scalars(
            select(Bot).where(Bot.status.in_(("running", "starting", "paused", "risk_halted")))
        )
    ]
    active = load_active_cycles(s, cur)
    pre = [
        _check(
            "bots_stopped",
            not running,
            "all bots stopped" if not running else f"stop these bots first: {', '.join(running)}",
        ),
        _check(
            "no_active_cycles",
            not active,
            "no active cycles"
            if not active
            else f"{len(active)} active cycle(s) in {cur.value} must finish or be closed first",
        ),
    ]
    if not all(c["passed"] for c in pre):
        with session_scope() as audit_s:
            audit(
                audit_s,
                "security",
                f"environment switch to {tgt.value} refused for {actor}",
                "warning",
                actor=actor,
                data={"checks": pre},
            )
        raise APIError(409, "environment_switch_refused", "environment switch refused", {"checks": pre})
    checks = list(pre)
    if tgt is Environment.LIVE:
        r = live_readiness(s, fetch_specs=True)
        checks += r["checks"]
        if not r["ready"]:
            # Own transaction: the request's session is rolled back when we raise.
            with session_scope() as audit_s:
                failed = [c["name"] for c in checks if not c["passed"]]
                audit(
                    audit_s,
                    "security",
                    f"live activation refused for {actor}: failed {', '.join(failed)}",
                    "warning",
                    actor=actor,
                    data={"checks": checks},
                )
            raise APIError(
                409,
                "live_not_ready",
                "live trading cannot be enabled: safety checks failed",
                {"checks": checks},
            )
        phrase = f"ENABLE LIVE TRADING {r['account_id']}"
        if (confirm_text or "").strip() != phrase:
            raise confirm_required(
                phrase,
                "LIVE TRADING places real orders with real money. Type the phrase to confirm the account and mode.",
                checks=checks,
                account_id=r["account_id"],
            )
        set_setting(s, "live_trading_enabled", True, actor)
        notify(
            s,
            "critical_error",
            f"LIVE trading enabled by {actor} on account {r['account_id']}",
            "critical",
            title="Live trading enabled",
        )
    else:
        set_setting(s, "live_trading_enabled", False, actor)
    set_setting(s, "environment", tgt.value, actor)
    audit(
        s,
        "security",
        f"environment switched {cur.value} -> {tgt.value} by {actor}",
        "warning" if tgt is Environment.LIVE else "info",
        actor=actor,
        data={"checks": checks},
    )
    return {"environment": tgt.value, "checks": checks}
