from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.core.security import constant_time_equals, token_hash
from app.db.base import get_db, utcnow
from app.models import User, UserSession

SESSION_COOKIE = "fx_session"
CSRF_COOKIE = "fx_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: User
    session: UserSession

    @property
    def name(self) -> str:
        return self.user.username

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"


def current_principal(request: Request, s: Session = Depends(get_db, scope="function")) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise APIError(401, "unauthenticated", "not signed in")
    sess = s.get(UserSession, token_hash(token))
    now = utcnow()
    if sess is None or sess.expires_at < now:
        raise APIError(401, "session_expired", "session expired; sign in again")
    user = s.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise APIError(401, "unauthenticated", "account disabled")
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not constant_time_equals(header, sess.csrf_token):
            raise APIError(403, "csrf_failed", "missing or invalid CSRF token")
    if now - sess.last_seen_at > timedelta(minutes=1):
        sess.last_seen_at = now
    return Principal(user, sess)


def require_admin(p: Principal = Depends(current_principal)) -> Principal:
    if not p.is_admin:
        raise APIError(403, "forbidden", "administrator role required")
    return p


class RateLimiter:
    """Sliding-window limiter for sensitive endpoints (per principal, per process)."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise APIError(
                    429,
                    "rate_limited",
                    "too many requests; slow down",
                    {"retry_after_seconds": int(self.window - (now - q[0])) + 1},
                )
            q.append(now)


trading_limiter = RateLimiter(30, 60)


def trading_rate_limit(p: Principal = Depends(require_admin)) -> Principal:
    trading_limiter.check(p.user.id)
    return p
