from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, backtests, bots, misc, risk, system, trading
from app.bootstrap import bootstrap
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, correlation_id

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Seeding only. Starting the API never starts a bot or places an order.
    bootstrap()
    yield


def create_app() -> FastAPI:
    st = get_settings()
    configure_logging(st.log_level)
    app = FastAPI(
        title="Forex Trading Platform API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if st.enable_docs and not st.is_production else None,
        openapi_url="/api/openapi.json" if st.enable_docs and not st.is_production else None,
        redoc_url=None,
    )
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=st.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Correlation-ID"],
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next):  # type: ignore[no-untyped-def]
        cid = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex[:16]
        token = correlation_id.set(cid[:64])
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    for r in (
        auth.router,
        system.router,
        bots.router,
        trading.router,
        risk.router,
        backtests.router,
        misc.router,
    ):
        app.include_router(r, prefix=API_PREFIX)
    return app


app = create_app()
