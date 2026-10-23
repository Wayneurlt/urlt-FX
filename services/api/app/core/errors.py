from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("api.errors")


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def not_found(what: str) -> APIError:
    return APIError(404, "not_found", f"{what} not found")


def conflict(message: str, code: str = "conflict", **details: Any) -> APIError:
    return APIError(409, code, message, details or None)


def bad_request(message: str, code: str = "invalid_request", **details: Any) -> APIError:
    return APIError(400, code, message, details or None)


def confirm_required(phrase: str, message: str, **details: Any) -> APIError:
    return APIError(409, "confirmation_required", message, {"required_confirm_text": phrase, **details})


def require_confirm(given: str | None, phrase: str, message: str, **details: Any) -> None:
    if (given or "").strip() != phrase:
        raise confirm_required(phrase, message, **details)


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            _body(code, str(exc.detail)), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [
            {"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body("validation_error", "request validation failed", {"errors": errs}), status_code=422
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            _body(
                "internal_error",
                "an internal error occurred; see server logs (correlation id in response headers)",
            ),
            status_code=500,
        )
