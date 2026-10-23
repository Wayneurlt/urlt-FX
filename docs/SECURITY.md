# Security

## Authentication & sessions
* Argon2id password hashing (`argon2-cffi`), minimum 12 characters. No default or hard-coded password:
  administrators are created with `scripts/create_admin.py` (or a one-time bootstrap env var).
* Random 256-bit session tokens in an HttpOnly, SameSite=Lax cookie (`Secure` required in production – startup
  fails otherwise). Only the SHA-256 of the token is stored. Sessions expire (`FX_SESSION_TTL_MINUTES`).
* CSRF: every non-GET request must send `X-CSRF-Token` matching the per-session token (compared in constant time).
* Brute force: 5 failures per username or IP within 15 minutes lock sign-in (429). All attempts are audited.
* Roles: `admin` (trade/configure) and `viewer` (read-only); checked on every protected endpoint.
* Trading endpoints are rate-limited per user (30/min).

## Secrets
* Integration credentials are encrypted with Fernet using `FX_SECRET_ENCRYPTION_KEY`, which exists only in the
  process environment. Without the key, credentials cannot be stored. `secrets.external_ref` is reserved for a
  secret-manager identifier.
* Production: inject the key from a secret manager (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault,
  HashiCorp Vault) – never commit `.env`.
* APIs never return secret values (`is_set` only). Logs pass through `redact()` (key names and token patterns).
* The frontend has no secrets: it uses same-origin `/api/v1` proxied server-side (`API_ORIGIN` is not `NEXT_PUBLIC_`).
* AI providers receive only aggregated metrics/records – never credentials, account ids or keys.

## Trading safety controls
* New installs start in **simulation**; bots start `disabled`; starting the API or worker never starts a bot.
* Live activation requires: admin action, verified MT5 integration, account validation, broker instrument specs,
  compatible margin mode, valid risk config, working reconciliation, broker-side stops, no critical breakers, and
  typing `ENABLE LIVE TRADING <account id>` (see `environment_service.py`). Refusals are audited.
* Starting a bot in live mode needs `START LIVE`; liquidation `LIQUIDATE`; loosening risk `CONFIRM RISK INCREASE`.
* No endpoint accepts arbitrary broker commands; all entries go through strategy/risk services.

## HTTP hardening
CORS restricted to `FX_CORS_ORIGINS` with credentials; `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy`, `Cache-Control: no-store` on API responses; OpenAPI docs disabled in production; error
responses never include stack traces (correlation id header for support).

## Audit
`audit_logs` records authentication, configuration changes (with before/after), lifecycle actions, risk decisions,
order intents/results, stop modifications, reconciliation and integration events with correlation ids.
