"""统一错误码 + 异常处理器（契约 §1）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

CODE_STATUS: dict[str, int] = {
    "INVALID_ARGUMENT": 400,
    "VALIDATION_ERROR": 422,
    "UNAUTHORIZED": 401,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "DEVICE_UNAVAILABLE": 503,
    "DEVICE_BUSY": 409,
    "SCRCPY_FAILED": 502,
    "UNSUPPORTED": 400,
    "INTERNAL": 500,
}


class ApiError(Exception):
    """业务异常：抛它，交给处理器统一出信封。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        if code not in CODE_STATUS:
            code = "INTERNAL"
        self.code = code
        self.message = message
        self.details = details if details is not None else {}
        self.status_code = status_code or CODE_STATUS[code]


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def not_found(what: str, ident: Any = None) -> ApiError:
    return ApiError("NOT_FOUND", f"{what} not found", {"id": ident})


def invalid(message: str, details: Any = None) -> ApiError:
    return ApiError("INVALID_ARGUMENT", message, details)


def conflict(message: str, details: Any = None) -> ApiError:
    return ApiError("CONFLICT", message, details)


def device_unavailable(message: str = "device unavailable", details: Any = None) -> ApiError:
    return ApiError("DEVICE_UNAVAILABLE", message, details)


def _fmt_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    items = []
    for err in errors:
        loc = [str(part) for part in err.get("loc", [])]
        items.append(
            {
                "path": ".".join(loc) if loc else "",
                "code": str(err.get("type", "validation_error")),
                "message": str(err.get("msg", "")),
            }
        )
    return items


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "request validation failed",
                {"errors": _fmt_validation_errors(exc.errors())},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        from .logs import logger

        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL", "internal error", {"type": type(exc).__name__}),
        )
