"""Optional AI commentary. The trading engine never depends on this module.

Guarantees:
* only aggregated metrics / records are sent - never credentials, account ids
  or secrets;
* output is returned as labelled commentary and is never acted upon;
* when no provider is configured, the API answers 409 and nothing is called.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.errors import APIError, not_found
from app.db.base import utcnow
from app.models import BacktestRun, IntegrationConfig, TradingCycle
from app.services.audit import audit
from app.services.integrations_service import ANTHROPIC_DEFAULT_MODEL, anthropic_client, get_secret

LABEL = "AI-generated commentary - analysis only, not financial advice, may be wrong. It cannot place trades or change settings."

SYSTEM = (
    "You review results from a forex trading research platform. Be candid and specific: point out small samples, "
    "cost sensitivity, over-fitting risk, and what further tests would be informative. Never claim a strategy is "
    "profitable or guaranteed, never recommend increasing risk to recover losses, and never give instructions to "
    "place trades. Answer in under 300 words with short headed sections."
)


def _subject_payload(s: Session, subject: str, obj_id: str) -> dict[str, Any]:
    if subject == "backtest":
        run = s.get(BacktestRun, obj_id)
        if run is None:
            raise not_found("backtest")
        if run.status != "succeeded":
            raise APIError(409, "not_ready", "backtest has not finished successfully")
        m = {k: v for k, v in (run.metrics or {}).items() if not isinstance(v, dict)}
        return {
            "kind": "backtest",
            "bot": run.bot_kind,
            "symbol": run.symbol,
            "period": [run.start.isoformat(), run.end.isoformat()],
            "data_source": run.data_source,
            "metrics": m,
            "by_month": (run.metrics or {}).get("by_month"),
            "warnings": run.warnings,
            "counters": run.counters,
            "costs": (run.config or {}).get("costs"),
            "risk": (run.config or {}).get("risk"),
        }
    if subject == "cycle":
        c = s.get(TradingCycle, obj_id)
        if c is None:
            raise not_found("cycle")
        return {
            "kind": "cycle",
            "type": c.kind,
            "symbol": c.symbol,
            "trigger": c.trigger_type,
            "state": c.state,
            "outcome": c.outcome,
            "realized_pnl": c.realized_pnl,
            "simulated": c.simulated,
            "legs": [
                {
                    "side": lg.side,
                    "fill": lg.fill_price,
                    "initial_sl": lg.intended_sl,
                    "exit": lg.exit_price,
                    "exit_reason": lg.exit_reason,
                    "net": lg.net_pnl,
                    "stop_moves": len(lg.stop_history or []),
                }
                for lg in c.legs
            ],
            "risk_checks": [ch for ch in (c.risk_decision or {}).get("checks", []) if not ch.get("passed")],
            "notes": c.notes,
        }
    if subject == "risk":
        from app.services.risk_service import active_breaker_rows
        from app.services.system_settings import current_environment

        env = current_environment(s)
        return {
            "kind": "risk_alerts",
            "breakers": [
                {
                    "type": b.type,
                    "scope": b.scope,
                    "reason": b.reason,
                    "critical": b.critical,
                    "recovery": b.recovery,
                }
                for b in active_breaker_rows(s, env)
            ],
        }
    raise APIError(400, "invalid_subject", "subject must be backtest, cycle or risk")


def _provider(s: Session) -> str | None:
    for key in ("anthropic", "openai"):
        row = s.get(IntegrationConfig, key)
        if row and row.enabled and row.status == "connected":
            return key
    return None


def analyze(s: Session, subject: str, obj_id: str, actor: str) -> dict[str, Any]:
    provider = _provider(s)
    if provider is None:
        raise APIError(
            409,
            "ai_not_configured",
            "no AI provider is enabled and verified; configure one under Integrations (optional)",
        )
    payload = _subject_payload(s, subject, obj_id)
    prompt = "Explain these results for the trader:\n\n" + json.dumps(payload, default=str, indent=1)
    if provider == "anthropic":
        model, content = _anthropic(s, prompt)
    else:
        model, content = _openai(s, prompt)
    audit(s, "ai", f"AI analysis of {subject} {obj_id} via {provider}", actor=actor, data={"model": model})
    return {
        "provider": provider,
        "model": model,
        "content": content,
        "label": LABEL,
        "generated_at": utcnow(),
    }


def _anthropic(s: Session, prompt: str) -> tuple[str, str]:
    import anthropic

    client = anthropic_client(s)
    if client is None:
        raise APIError(409, "ai_not_configured", "Anthropic API key not set")
    model = (s.get(IntegrationConfig, "anthropic").settings or {}).get("model") or ANTHROPIC_DEFAULT_MODEL  # type: ignore[union-attr]
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM,
            output_config={"effort": "low"},
            # Server-side refusal fallback (routes a declined request to another model by category).
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise APIError(502, "ai_error", "Anthropic rejected the API key") from exc
    except anthropic.RateLimitError as exc:
        raise APIError(429, "ai_rate_limited", "Anthropic rate limit reached; try again later") from exc
    except anthropic.APIStatusError as exc:
        raise APIError(502, "ai_error", f"Anthropic API error {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise APIError(502, "ai_unreachable", "could not reach the Anthropic API") from exc
    if resp.stop_reason == "refusal":
        raise APIError(422, "ai_refused", "the model declined to analyse this content")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return resp.model, text or "(no text returned)"


def _openai(s: Session, prompt: str) -> tuple[str, str]:
    row = s.get(IntegrationConfig, "openai")
    model = (row.settings or {}).get("model") if row else None
    key = get_secret(s, "openai", "api_key")
    if not (model and key):
        raise APIError(409, "ai_not_configured", "OpenAI model/key not set")
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        timeout=60,
        json={
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        },
    )
    if r.status_code != 200:
        raise APIError(502, "ai_error", f"OpenAI API error {r.status_code}")
    return model, r.json()["choices"][0]["message"]["content"]
