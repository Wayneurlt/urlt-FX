"""Cross-process leases backed by the database (works on SQLite and PostgreSQL).

All trading operations for an environment (worker ticks, manual entries,
liquidations) run while holding ``trading:<environment>``, so the simulated
broker state, cycle state and risk reservations are never mutated
concurrently by two processes.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import cast

from sqlalchemy import or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from app.core.errors import APIError
from app.db.base import session_scope, utcnow
from app.models import Lease

OWNER = f"{socket.gethostname()}:{os.getpid()}"


class LeaseUnavailable(APIError):
    def __init__(self, name: str) -> None:
        super().__init__(409, "busy", f"another trading operation is in progress ({name}); retry shortly")


def _ensure(name: str) -> None:
    with session_scope() as s:
        if s.get(Lease, name) is None:
            try:
                s.add(Lease(name=name))
                s.flush()
            except IntegrityError:
                s.rollback()


def try_acquire(name: str, lease_seconds: int, owner: str) -> bool:
    _ensure(name)
    now = utcnow()
    with session_scope() as s:
        res = s.execute(
            update(Lease)
            .where(
                Lease.name == name, or_(Lease.owner.is_(None), Lease.expires_at < now, Lease.owner == owner)
            )
            .values(owner=owner, expires_at=now + timedelta(seconds=lease_seconds))
        )
        return cast(CursorResult, res).rowcount == 1


def release(name: str, owner: str) -> None:
    with session_scope() as s:
        s.execute(
            update(Lease).where(Lease.name == name, Lease.owner == owner).values(owner=None, expires_at=None)
        )


@contextmanager
def lease(name: str, lease_seconds: int = 60, wait_seconds: float = 10.0) -> Iterator[None]:
    owner = f"{OWNER}:{threading.get_ident()}"
    deadline = time.monotonic() + wait_seconds
    while not try_acquire(name, lease_seconds, owner):
        if time.monotonic() > deadline:
            raise LeaseUnavailable(name)
        time.sleep(0.1)
    try:
        yield
    finally:
        release(name, owner)
