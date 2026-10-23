from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import CSRF_COOKIE, SESSION_COOKIE, Principal, current_principal, require_admin
from app.core.config import get_settings
from app.core.errors import APIError, bad_request
from app.core.security import hash_password, new_token, token_hash, verify_password
from app.db.base import get_db, utcnow
from app.models import LoginAttempt, User, UserSession
from app.services.audit import audit

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


def user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "timezone": u.timezone,
        "display_name": u.display_name,
    }


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    hops = get_settings().trusted_proxy_hops
    if not hops:
        return peer
    # Each trusted proxy appends the address it received the request from, so the client is
    # ``hops`` entries from the right; anything further left could be forged by the client.
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if not chain:
        return peer
    return chain[-hops] if len(chain) >= hops else chain[0]


@router.post("/auth/login")
def login(
    body: LoginIn, request: Request, response: Response, s: Session = Depends(get_db, scope="function")
) -> dict:
    st = get_settings()
    ip = _client_ip(request)
    since = utcnow() - timedelta(minutes=st.login_lockout_minutes)
    fails = (
        s.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.success.is_(False),
                LoginAttempt.created_at >= since,
                (LoginAttempt.username == body.username) | (LoginAttempt.ip == ip),
            )
        )
        or 0
    )
    if fails >= st.login_max_failures:
        audit(s, "auth", f"login locked out for {body.username!r} from {ip}", "warning")
        raise APIError(
            429,
            "too_many_attempts",
            "too many failed sign-in attempts; try again later",
            {"retry_after_seconds": st.login_lockout_minutes * 60},
        )
    user = s.scalars(select(User).where(User.username == body.username)).first()
    ok = user is not None and user.is_active and verify_password(user.password_hash, body.password)
    s.add(LoginAttempt(username=body.username, ip=ip, success=ok))
    if not ok:
        audit(s, "auth", f"failed sign-in for {body.username!r} from {ip}", "warning")
        s.commit()
        raise APIError(401, "invalid_credentials", "invalid username or password")
    assert user is not None
    token, csrf = new_token(), new_token(24)
    expires = utcnow() + timedelta(minutes=st.session_ttl_minutes)
    s.add(
        UserSession(
            id=token_hash(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=expires,
            ip=ip,
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    audit(s, "auth", f"{user.username} signed in from {ip}", actor=user.username)
    max_age = st.session_ttl_minutes * 60
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=st.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=st.cookie_secure, samesite="lax", path="/"
    )
    return {"user": user_out(user), "csrf_token": csrf}


@router.post("/auth/logout")
def logout(
    response: Response,
    p: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    s.delete(p.session)
    audit(s, "auth", f"{p.name} signed out", actor=p.name)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    return {"user": user_out(p.user), "csrf_token": p.session.csrf_token}


class SettingsIn(BaseModel):
    timezone: str | None = Field(None, max_length=64)
    display_name: str | None = Field(None, max_length=100)


@router.get("/settings")
def get_user_settings(p: Principal = Depends(current_principal)) -> dict:
    return {"timezone": p.user.timezone, "display_name": p.user.display_name}


@router.put("/settings")
def put_user_settings(
    body: SettingsIn,
    p: Principal = Depends(current_principal),
    s: Session = Depends(get_db, scope="function"),
) -> dict:
    if body.timezone is not None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise bad_request(f"unknown timezone {body.timezone}") from exc
        p.user.timezone = body.timezone
    if body.display_name is not None:
        p.user.display_name = body.display_name
    s.add(p.user)
    return {"timezone": p.user.timezone, "display_name": p.user.display_name}


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=12, max_length=200)
    role: str = Field("viewer", pattern="^(admin|viewer)$")


@router.get("/users")
def list_users(
    _: Principal = Depends(require_admin), s: Session = Depends(get_db, scope="function")
) -> list[dict]:
    return [
        user_out(u) | {"is_active": u.is_active, "created_at": u.created_at}
        for u in s.scalars(select(User).order_by(User.username))
    ]


@router.post("/users")
def create_user(
    body: UserIn, p: Principal = Depends(require_admin), s: Session = Depends(get_db, scope="function")
) -> dict:
    if s.scalars(select(User).where(User.username == body.username)).first():
        raise APIError(409, "exists", "username already exists")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    s.add(u)
    s.flush()
    audit(s, "auth", f"user {u.username} ({u.role}) created by {p.name}", actor=p.name)
    return user_out(u)
