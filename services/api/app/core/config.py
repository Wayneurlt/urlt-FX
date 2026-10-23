from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """Process configuration from environment variables (prefix ``FX_``).

    Trading environment (simulation/paper/live) is NOT set here: it is a
    persisted, audited system setting that can only become ``live`` through
    the live-readiness checks. ``app_env`` only selects deployment behaviour.
    """

    model_config = SettingsConfigDict(
        env_prefix="FX_", env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["development", "test", "paper", "production"] = "development"
    database_url: str = Field(
        default_factory=lambda: f"sqlite:///{(REPO_ROOT / 'data' / 'fx.db').as_posix()}"
    )
    data_dir: Path = Field(default_factory=lambda: REPO_ROOT / "data")

    # Secret used to encrypt integration credentials at rest (Fernet key). Never stored in the DB.
    secret_encryption_key: SecretStr | None = None

    session_ttl_minutes: int = 480
    cookie_secure: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    market_data_provider: Literal["synthetic"] = "synthetic"
    synthetic_seed: int = 7
    sample_calendar_path: Path = Field(default_factory=lambda: REPO_ROOT / "data" / "sample_events.json")

    worker_tick_seconds: float = 2.0
    worker_job_poll_seconds: float = 1.0
    trading_lock_lease_seconds: int = 60
    audit_retention_days: int = 365

    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: SecretStr | None = None

    log_level: str = "INFO"
    enable_docs: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and not s.cookie_secure:
        raise RuntimeError("FX_COOKIE_SECURE must be true in production")
    return s
