"""Create (or reset the password of) an administrator account.

Usage:
    python scripts/create_admin.py <username>            # prompts for the password
    FX_ADMIN_PASSWORD=... python scripts/create_admin.py <username> --from-env

There is deliberately no default password.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from sqlalchemy import select  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.core.security import MIN_PASSWORD_LENGTH, hash_password  # noqa: E402
from app.db.base import session_scope  # noqa: E402
from app.models import User  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("username")
    ap.add_argument("--role", choices=["admin", "viewer"], default="admin")
    ap.add_argument("--from-env", action="store_true", help="read the password from FX_ADMIN_PASSWORD")
    args = ap.parse_args()
    if args.from_env:
        pw = os.environ.get("FX_ADMIN_PASSWORD", "")
    else:
        pw = getpass.getpass(f"Password for {args.username} (min {MIN_PASSWORD_LENGTH} chars): ")
        if pw != getpass.getpass("Repeat password: "):
            print("passwords do not match", file=sys.stderr)
            return 1
    if len(pw) < MIN_PASSWORD_LENGTH:
        print(f"password must be at least {MIN_PASSWORD_LENGTH} characters", file=sys.stderr)
        return 1
    bootstrap()
    with session_scope() as s:
        u = s.scalars(select(User).where(User.username == args.username)).first()
        if u is None:
            s.add(User(username=args.username, password_hash=hash_password(pw), role=args.role))
            print(f"created {args.role} user {args.username}")
        else:
            u.password_hash = hash_password(pw)
            u.role = args.role
            print(f"updated password/role for {args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
