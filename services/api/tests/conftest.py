from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="fxtest-"))
os.environ["FX_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["FX_DATA_DIR"] = str(_TMP)
os.environ["FX_APP_ENV"] = "test"
os.environ["FX_SAMPLE_CALENDAR_PATH"] = str(_TMP / "no_events.json")
from cryptography.fernet import Fernet  # noqa: E402

os.environ["FX_SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base, get_engine, session_scope  # noqa: E402
from app.models import User  # noqa: E402
from trading.market_data.provider import MarketStatus  # noqa: E402

ADMIN = ("admin", "correct-horse-battery")
VIEWER = ("viewer", "viewer-password-123")


@pytest.fixture(autouse=True)
def no_real_mt5(monkeypatch):
    """Never let tests reach a real MetaTrader 5 terminal installed on this machine."""
    from app.services import integrations_service
    from trading.adapters.mt5 import adapter as mt5_adapter

    def blocked():
        raise mt5_adapter.MT5Unavailable("real MetaTrader5 is blocked in tests")

    monkeypatch.setattr(mt5_adapter, "load_mt5_module", blocked)
    monkeypatch.setattr(integrations_service, "mt5_package_available", lambda: False)


@pytest.fixture(autouse=True)
def fx_market_always_open(monkeypatch):
    """The synthetic feed honours the real FX weekend; tests must not depend on the wall clock."""
    monkeypatch.setattr("trading.market_data.synthetic.fx_market_open", lambda now: MarketStatus(True))


@pytest.fixture()
def db():
    eng = get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    bootstrap()
    with session_scope() as s:
        s.add(User(username=ADMIN[0], password_hash=hash_password(ADMIN[1]), role="admin"))
        s.add(User(username=VIEWER[0], password_hash=hash_password(VIEWER[1]), role="viewer"))
    yield
    with session_scope() as s:
        s.execute(delete(User))


class Api:
    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.csrf = ""

    def login(self, creds=ADMIN) -> dict:
        r = self.c.post("/api/v1/auth/login", json={"username": creds[0], "password": creds[1]})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        return r.json()

    def get(self, path: str, **kw):
        return self.c.get("/api/v1" + path, **kw)

    def post(self, path: str, json=None, **kw):
        return self.c.post("/api/v1" + path, json=json, headers={"X-CSRF-Token": self.csrf}, **kw)

    def put(self, path: str, json=None, **kw):
        return self.c.put("/api/v1" + path, json=json, headers={"X-CSRF-Token": self.csrf}, **kw)


@pytest.fixture()
def client(db):
    from app.auth.deps import trading_limiter
    from app.main import app

    trading_limiter.hits.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def api(client) -> Api:
    a = Api(client)
    a.login()
    return a


@pytest.fixture()
def loose_spreads(api):
    """Synthetic spreads widen during bursts; widen the limit so entry tests are deterministic."""
    cfg = api.get("/risk/config").json()["config"]
    cfg["global"]["default_max_spread_pips"] = 50
    r = api.put("/risk/config", json={"config": cfg, "confirm_text": "CONFIRM RISK INCREASE"})
    assert r.status_code == 200, r.text
    return api
