"""Start an automated paper-trading session for one bot (explicit, opt-in).

    python scripts/paper_session.py news-volatility
    python scripts/paper_session.py daily-trading --stop

Switches the platform to the PAPER environment if possible (refused while bots
are running or cycles are active), enables the bot and requests a start. The
worker (`make worker`) must be running: it performs the pre-start checks and
then evaluates the bot on every tick. Nothing here can touch a live account.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from app.bootstrap import bootstrap  # noqa: E402
from app.core.errors import APIError  # noqa: E402
from app.db.base import session_scope  # noqa: E402
from app.services.bots_service import apply_action, get_bot  # noqa: E402
from app.services.environment_service import switch_environment  # noqa: E402
from app.services.system_settings import current_environment  # noqa: E402
from trading.interfaces.types import BotStatus, Environment  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bot_id", choices=["news-volatility", "daily-trading"])
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()
    bootstrap()
    with session_scope() as s:
        env = current_environment(s)
        if env is Environment.LIVE:
            print("refusing: the platform is in LIVE mode", file=sys.stderr)
            return 2
        bot = get_bot(s, args.bot_id)
        try:
            if args.stop:
                if bot.status not in (BotStatus.STOPPED.value, BotStatus.DISABLED.value):
                    apply_action(s, bot, "stop", "cli", env, None)
                print(f"{bot.name}: {bot.status}")
                return 0
            if env is not Environment.PAPER:
                switch_environment(s, "paper", "cli", None)
                env = Environment.PAPER
            if bot.status == BotStatus.DISABLED.value:
                apply_action(s, bot, "enable", "cli", env, None)
            if bot.status in (BotStatus.STOPPED.value, BotStatus.ERROR.value):
                apply_action(s, bot, "start", "cli", env, None)
        except APIError as exc:
            print(f"refused: {exc.message} {exc.details or ''}", file=sys.stderr)
            return 1
        print(
            f"{bot.name}: {bot.status} in {env.value}. The worker will run pre-start checks on its next tick."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
