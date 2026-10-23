"""Idempotent seeding of reference data. Never creates a default password."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.base import Base, get_engine, session_scope
from app.models import Role, User
from app.services.bots_service import seed_bots
from app.services.instruments import seed_instruments
from app.services.integrations_service import seed_integrations
from app.services.risk_service import seed_risk_config

log = logging.getLogger("bootstrap")

ROLES = {
    "admin": (
        "Full control: bots, risk settings, integrations, environment",
        ["read", "trade", "configure", "admin"],
    ),
    "viewer": ("Read-only access", ["read"]),
}


def bootstrap(create_tables: bool = False) -> None:
    if create_tables:
        Base.metadata.create_all(get_engine())
    with session_scope() as s:
        for name, (desc, perms) in ROLES.items():
            if s.get(Role, name) is None:
                s.add(Role(name=name, description=desc, permissions=perms))
        s.flush()
        seed_instruments(s)
        seed_bots(s)
        seed_risk_config(s)
        seed_integrations(s)
        st = get_settings()
        if (
            st.bootstrap_admin_username
            and st.bootstrap_admin_password
            and s.scalars(select(User).limit(1)).first() is None
        ):
            s.add(
                User(
                    username=st.bootstrap_admin_username,
                    password_hash=hash_password(st.bootstrap_admin_password.get_secret_value()),
                    role="admin",
                )
            )
            log.info("bootstrap admin user %s created from environment", st.bootstrap_admin_username)
