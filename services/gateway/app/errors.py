"""Error envelope {error:{code,message,details?},request_id} (gateway.openapi.yaml#/components/schemas/Error)."""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

STATUS_CODE = {400: "VALIDATION_ERROR", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "NOT_FOUND",
               409: "CONFLICT", 429: "RATE_LIMITED"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None, headers=None):
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def rid(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex


def error_response(request: Request, status: int, code: str, message: str, details=None, headers=None):
    err = {"code": code, "message": message}
    if details:
        err["details"] = details
    h = {"X-Request-Id": rid(request), **(headers or {})}
    return JSONResponse({"error": err, "request_id": rid(request)}, status, headers=h)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        resp = await call_next(request)
        resp.headers.setdefault("X-Request-Id", request.state.request_id)
        return resp

    @app.exception_handler(ApiError)
    async def _api(request: Request, e: ApiError):
        return error_response(request, e.status, e.code, e.message, e.details, e.headers)

    @app.exception_handler(RequestValidationError)
    async def _val(request: Request, e: RequestValidationError):
        return error_response(request, 400, "VALIDATION_ERROR", "invalid request",
                              {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()][:20]})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        code = STATUS_CODE.get(e.status_code, "INTERNAL")
        status = 404 if e.status_code == 405 else e.status_code  # no route for this method == not proxied
        return error_response(request, status, code, str(e.detail) if e.status_code != 405 else "not found")

    @app.exception_handler(Exception)
    async def _any(request: Request, e: Exception):
        return error_response(request, 500, "INTERNAL", "internal error")
