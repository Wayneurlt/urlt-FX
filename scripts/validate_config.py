"""Validate a YAML configuration file against the real schemas.

python scripts/validate_config.py config/sample.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from trading.risk.config import RiskConfig
from trading.strategies.daily_trading.config import DailyTradingConfig
from trading.strategies.news_volatility.config import NewsVolatilityConfig


def main(path: str) -> int:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    ok = True
    for key, model in (
        ("news_volatility", NewsVolatilityConfig),
        ("daily_trading", DailyTradingConfig),
        ("risk", RiskConfig),
    ):
        try:
            model.model_validate(data.get(key) or {})
            print(f"{key}: valid")
        except ValidationError as exc:
            ok = False
            print(f"{key}: INVALID\n{exc}")
    if data.get("execution", {}).get("live_trading_enabled"):
        print("note: live trading cannot be enabled from a file; use the live-readiness flow")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "config/sample.yaml"))
