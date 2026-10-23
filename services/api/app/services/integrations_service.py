"""Integration configuration, secret handling and real connection checks.

A connection is only reported as ``connected`` after an actual check
succeeded. Entering configuration alone leaves the status ``unverified``.
"""

from __future__ import annotations

import importlib.util
import smtplib
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.core.security import SecretsUnavailable, decrypt_secret, encrypt_secret
from app.db.base import utcnow
from app.models import IntegrationConfig, SecretRef
from app.services.audit import audit

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"

DEFINITIONS: dict[str, dict[str, Any]] = {
    "mt5": {
        "name": "MetaTrader 5",
        "category": "broker",
        "supports_test": True,
        "fields": [
            ("login", "Account login", False, True),
            ("server", "Trade server", False, True),
            ("terminal_path", "Terminal path (optional)", False, False),
            ("symbol_suffix", "Broker symbol suffix", False, False),
            ("password", "Account password", True, True),
        ],
        "docs": "Requires Windows, an installed and logged-in MT5 terminal and the MetaTrader5 Python package. See docs/MT5_INTEGRATION.md. Live trading also requires the live-readiness checks.",
    },
    "market_data": {
        "name": "Market data provider",
        "category": "market_data",
        "supports_test": True,
        "fields": [("provider", "Provider (synthetic)", False, True)],
        "docs": "The built-in provider generates SYNTHETIC prices for simulation and paper trading. It is not real market data. Real data comes from the broker connection in live mode; other vendors can be added behind trading/market_data/provider.py.",
    },
    "economic_calendar": {
        "name": "Economic calendar",
        "category": "calendar",
        "supports_test": True,
        "fields": [
            ("provider", "Provider (none | sample)", False, True),
            ("path", "Sample events file", False, False),
        ],
        "docs": "No real calendar vendor is bundled. 'sample' loads a local JSON file whose events are labelled SAMPLE and are never real upcoming news. While no fresh calendar is available, news-triggered entries are blocked.",
    },
    "email": {
        "name": "Email notifications (SMTP)",
        "category": "notifications",
        "supports_test": True,
        "fields": [
            ("host", "SMTP host", False, True),
            ("port", "SMTP port", False, True),
            ("username", "Username", False, False),
            ("from_address", "From address", False, True),
            ("to_address", "To address", False, True),
            ("password", "SMTP password", True, False),
        ],
        "docs": "Optional. The test performs a real SMTP connection/login; no message is sent during the test.",
    },
    "telegram": {
        "name": "Telegram notifications",
        "category": "notifications",
        "supports_test": True,
        "fields": [("chat_id", "Chat id", False, True), ("bot_token", "Bot token", True, True)],
        "docs": "Optional. The test calls the Telegram getMe endpoint with the stored token.",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "category": "ai",
        "supports_test": True,
        "fields": [
            ("model", f"Model (default {ANTHROPIC_DEFAULT_MODEL})", False, False),
            ("api_key", "API key", True, True),
        ],
        "docs": "Optional. Used only for commentary (backtest/risk/cycle explanations). AI output is labelled as analysis and can never place trades or change risk limits.",
    },
    "openai": {
        "name": "OpenAI",
        "category": "ai",
        "supports_test": True,
        "fields": [("model", "Model", False, True), ("api_key", "API key", True, True)],
        "docs": "Optional alternative AI provider for commentary only.",
    },
}


def mt5_package_available() -> bool:
    return importlib.util.find_spec("MetaTrader5") is not None


def seed_integrations(s: Session) -> None:
    for key in DEFINITIONS:
        if s.get(IntegrationConfig, key) is None:
            row = IntegrationConfig(key=key, enabled=False, settings={}, status="not_configured")
            if key == "market_data":
                row.enabled, row.settings, row.status, row.detail = (
                    True,
                    {"provider": "synthetic"},
                    "connected",
                    "built-in SYNTHETIC price generator (not real market data)",
                )
            if key == "economic_calendar":
                row.settings = {"provider": "none"}
                row.detail = "no calendar provider configured; news entries blocked"
            s.add(row)


def _secrets(s: Session, key: str) -> dict[str, SecretRef]:
    return {r.name: r for r in s.scalars(select(SecretRef).where(SecretRef.integration_key == key))}


def get_secret(s: Session, key: str, name: str) -> str | None:
    ref = _secrets(s, key).get(name)
    if ref is None or ref.ciphertext is None:
        return None
    return decrypt_secret(ref.ciphertext)


def _configured(s: Session, row: IntegrationConfig) -> bool:
    d = DEFINITIONS[row.key]
    secrets = _secrets(s, row.key)
    for name, _label, secret, required in d["fields"]:
        if not required:
            continue
        if secret:
            if name not in secrets or not secrets[name].ciphertext:
                return False
        elif not (row.settings or {}).get(name):
            return False
    return True


def integration_out(s: Session, row: IntegrationConfig) -> dict[str, Any]:
    d = DEFINITIONS[row.key]
    secrets = _secrets(s, row.key)
    fields = []
    for name, label, secret, required in d["fields"]:
        fields.append(
            {
                "name": name,
                "label": label,
                "secret": secret,
                "required": required,
                "value": None if secret else (row.settings or {}).get(name),
                "is_set": bool(secrets.get(name) and secrets[name].ciphertext)
                if secret
                else bool((row.settings or {}).get(name)),
            }
        )
    configured = _configured(s, row)
    status = row.status if configured or row.key == "market_data" else "not_configured"
    if row.key == "mt5" and not mt5_package_available() and status != "not_configured":
        status = "unavailable"
    return {
        "key": row.key,
        "name": d["name"],
        "category": d["category"],
        "configured": configured,
        "enabled": row.enabled,
        "status": status,
        "last_check": row.last_check,
        "detail": row.detail,
        "fields": fields,
        "docs": d["docs"],
        "supports_test": d["supports_test"],
    }


def list_integrations(s: Session) -> list[dict[str, Any]]:
    return [
        integration_out(s, r) for r in s.scalars(select(IntegrationConfig).order_by(IntegrationConfig.key))
    ]


def update_integration(
    s: Session, key: str, enabled: bool, settings: dict[str, Any], secrets: dict[str, str], actor: str
) -> dict[str, Any]:
    row = s.get(IntegrationConfig, key)
    if row is None or key not in DEFINITIONS:
        raise not_found("integration")
    d = DEFINITIONS[key]
    allowed = {n: secret for n, _l, secret, _r in d["fields"]}
    for k in settings:
        if k not in allowed or allowed[k]:
            raise bad_request(f"unknown or secret field in settings: {k}")
    for k in secrets:
        if k not in allowed or not allowed[k]:
            raise bad_request(f"unknown secret field: {k}")
    if key == "market_data" and settings.get("provider", "synthetic") != "synthetic":
        raise bad_request("only the built-in synthetic provider is available")
    if key == "economic_calendar" and settings.get("provider", "none") not in ("none", "sample"):
        raise bad_request("calendar provider must be 'none' or 'sample'")
    row.settings = {**(row.settings or {}), **{k: v for k, v in settings.items() if v not in (None, "")}}
    existing = _secrets(s, key)
    for name, value in secrets.items():
        if not value:
            continue
        try:
            ct = encrypt_secret(value)
        except SecretsUnavailable as exc:
            raise bad_request(str(exc), "secrets_unavailable") from exc
        ref = existing.get(name)
        if ref is None:
            s.add(SecretRef(integration_key=key, name=name, ciphertext=ct))
        else:
            ref.ciphertext = ct
            ref.updated_at = utcnow()
    row.enabled = enabled
    s.flush()
    # Configuration changed: the previous verification no longer applies.
    if key != "market_data":
        row.status = "unverified" if _configured(s, row) else "not_configured"
        row.detail = (
            "configuration saved; run a connection test" if _configured(s, row) else "required fields missing"
        )
    if key == "economic_calendar" and row.settings.get("provider") == "sample":
        row.status, row.detail = "connected", "SAMPLE events file (not real news)"
    row.updated_at = utcnow()
    audit(
        s,
        "integration",
        f"integration {key} updated (enabled={enabled}; secrets changed: {sorted(k for k, v in secrets.items() if v)})",
        actor=actor,
        data={"settings": row.settings},
    )
    return integration_out(s, row)


def test_integration(s: Session, key: str, actor: str) -> dict[str, Any]:
    row = s.get(IntegrationConfig, key)
    if row is None or key not in DEFINITIONS:
        raise not_found("integration")
    ok, detail = False, ""
    try:
        if not _configured(s, row) and key != "market_data":
            ok, detail = False, "required configuration is missing"
        elif key == "market_data":
            ok, detail = True, "built-in SYNTHETIC generator available (not real market data)"
        elif key == "economic_calendar":
            ok, detail = _test_calendar(s, row)
        elif key == "mt5":
            ok, detail = _test_mt5(s, row)
        elif key == "email":
            ok, detail = _test_smtp(s, row)
        elif key == "telegram":
            r = httpx.get(f"https://api.telegram.org/bot{get_secret(s, key, 'bot_token')}/getMe", timeout=10)
            ok, detail = r.status_code == 200 and r.json().get("ok", False), f"HTTP {r.status_code}"
        elif key == "anthropic":
            ok, detail = _test_anthropic(s)
        elif key == "openai":
            r = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {get_secret(s, key, 'api_key')}"},
                timeout=15,
            )
            ok, detail = r.status_code == 200, f"HTTP {r.status_code}"
    except SecretsUnavailable as exc:
        ok, detail = False, str(exc)
    except Exception as exc:  # noqa: BLE001 - surface a safe message, never the secret
        ok, detail = False, f"{type(exc).__name__}: connection test failed"
    row.status = "connected" if ok else ("unavailable" if "not installed" in detail else "disconnected")
    row.detail = detail[:500]
    row.last_check = utcnow()
    audit(
        s,
        "integration",
        f"integration {key} test: {row.status} ({row.detail})",
        "info" if ok else "warning",
        actor=actor,
    )
    return integration_out(s, row)


def _test_calendar(s: Session, row: IntegrationConfig) -> tuple[bool, str]:
    from app.services.broker_service import calendar_provider

    prov = calendar_provider(s)
    if prov is None:
        return False, "calendar disabled"
    h = prov.health(utcnow())
    return h.available, h.detail


def _test_mt5(s: Session, row: IntegrationConfig) -> tuple[bool, str]:
    if not mt5_package_available():
        return False, "MetaTrader5 package not installed (Windows + MT5 terminal required)"
    adapter = build_mt5_adapter(s, require_enabled=False)
    if adapter is None:
        return False, "MT5 not configured"
    st = adapter.connect()
    if not st.connected:
        return False, _mt5_hint(st.detail)
    try:
        acct = adapter.account_info()
        parts = [
            f"connected to {acct.server}, account {acct.account_id}",
            "DEMO" if acct.is_demo else "REAL MONEY",
            f"{acct.currency}, margin mode {acct.margin_mode.value}",
            st.detail.removeprefix("connected; "),
        ]
        warnings = []
        term = adapter.mt5.terminal_info() if adapter.mt5 is not None else None
        if term is not None and not getattr(term, "trade_allowed", True):
            warnings.append("Algo Trading is OFF in the terminal (toolbar button) - orders will be refused")
        if acct.margin_mode.value != "hedging":
            warnings.append("account is not hedging - the News & Volatility bot cannot run on it")
        return True, "; ".join(parts) + ("" if not warnings else " | WARNING: " + "; ".join(warnings))
    finally:
        adapter.disconnect()


def _mt5_hint(detail: str) -> str:
    """Turn the common MT5 initialize errors into an actionable message."""
    if "-10003" in detail or "not found" in detail:
        return f"{detail} - install the MetaTrader 5 terminal on this machine (or set the terminal path)"
    if "-6" in detail or "Authorization" in detail:
        return f"{detail} - check login, password and the exact server name shown in MT5"
    if "-10005" in detail or "timeout" in detail.lower():
        return f"{detail} - open the MT5 terminal and make sure it is logged in and connected"
    return detail


def _test_smtp(s: Session, row: IntegrationConfig) -> tuple[bool, str]:
    st = row.settings or {}
    with smtplib.SMTP(st["host"], int(st["port"]), timeout=10) as smtp:
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls()
        pw = get_secret(s, "email", "password")
        if st.get("username") and pw:
            smtp.login(st["username"], pw)
    return True, "SMTP connection and login succeeded"


def anthropic_client(s: Session):  # type: ignore[no-untyped-def]
    import anthropic

    key = get_secret(s, "anthropic", "api_key")
    if not key:
        return None
    return anthropic.Anthropic(api_key=key, max_retries=2, timeout=60.0)


def _test_anthropic(s: Session) -> tuple[bool, str]:
    import anthropic

    client = anthropic_client(s)
    if client is None:
        return False, "API key not set"
    try:
        model = (s.get(IntegrationConfig, "anthropic").settings or {}).get("model") or ANTHROPIC_DEFAULT_MODEL  # type: ignore[union-attr]
        m = client.models.retrieve(model)
        return True, f"authenticated; model {m.id} available"
    except anthropic.AuthenticationError:
        return False, "invalid API key"
    except anthropic.NotFoundError:
        return False, "model not found for this key"
    except anthropic.APIConnectionError:
        return False, "network error reaching the Anthropic API"
    except anthropic.APIStatusError as exc:
        return False, f"API error {exc.status_code}"


def build_mt5_adapter(s: Session, require_enabled: bool = True):  # type: ignore[no-untyped-def]
    row = s.get(IntegrationConfig, "mt5")
    if row is None or (require_enabled and (not row.enabled or row.status != "connected")):
        return None
    st = row.settings or {}
    pw = get_secret(s, "mt5", "password")
    if not (st.get("login") and st.get("server") and pw):
        return None
    from trading.adapters.mt5.adapter import MT5Adapter, MT5Settings

    return MT5Adapter(
        MT5Settings(
            login=int(st["login"]),
            server=st["server"],
            password_secret=pw,
            terminal_path=st.get("terminal_path") or None,
            symbol_suffix=st.get("symbol_suffix") or "",
        )
    )
