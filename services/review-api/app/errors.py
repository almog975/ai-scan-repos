"""Contract Error envelope: {"error": {"code", "message", "details"?}, "request_id"}.

Every non-2xx JSON response goes through here so it validates against
services/contracts/review-api.openapi.yaml#/components/schemas/Error.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

CODES = ("INVALID_SIGNATURE", "CLOUD_ACK_REQUIRED", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND",
         "VALIDATION_ERROR", "RATE_LIMITED", "CONFLICT", "REPORT_EXPIRED", "PROVIDER_IN_USE",
         "AI_PROVIDER_UNREACHABLE", "AI_PROVIDER_AUTH_FAILED", "AI_PROVIDER_BAD_RESPONSE", "INTERNAL")
_BY_STATUS = {400: "VALIDATION_ERROR", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
              405: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None,
                 headers: dict | None = None):
        assert code in CODES, code
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def unauthorized(msg="authentication required"):
    return ApiError(401, "UNAUTHORIZED", msg)


def forbidden(msg="admin role required"):
    return ApiError(403, "FORBIDDEN", msg)


def not_found(what="resource"):
    return ApiError(404, "NOT_FOUND", f"{what} not found")


def conflict(msg, **details):
    return ApiError(409, "CONFLICT", msg, details or None)


def validation(msg, **details):
    return ApiError(400, "VALIDATION_ERROR", msg, details or None)


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex


def envelope(request: Request, code: str, message: str, details: dict | None = None) -> dict:
    err = {"code": code, "message": message}
    if details:
        err["details"] = details
    return {"error": err, "request_id": request_id(request)}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid[:128]
        resp = await call_next(request)
        resp.headers["X-Request-Id"] = request.state.request_id
        return resp

    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):
        return JSONResponse(envelope(request, exc.code, exc.message, exc.details), exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _BY_STATUS.get(exc.status_code, "INTERNAL")
        status = 404 if exc.status_code == 405 else exc.status_code
        return JSONResponse(envelope(request, code, str(exc.detail)), status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errs = [{"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(envelope(request, "VALIDATION_ERROR", "request validation failed", {"errors": errs}), 400)

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception):  # pragma: no cover - last resort
        log.exception("unhandled error")
        return JSONResponse(envelope(request, "INTERNAL", "internal error"), 500)
