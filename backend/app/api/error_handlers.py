from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.responses import error_response, request_id_of
from app.exceptions import AppError
from app.observability.logging import get_logger

logger = get_logger("app.errors")

_HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_ERROR",
    403: "AUTHORIZATION_ERROR",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "RATE_LIMIT_EXCEEDED",
}


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(AppError, exc)
    return error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    return error_response(request, status_code=exc.status_code, code=code, message=str(exc.detail))


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    fields = [
        {"location": [str(part) for part in error["loc"]], "message": error["msg"]}
        for error in exc.errors()
    ]
    return error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details={"fields": fields},
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled exception",
        exc_info=exc,
        extra={
            "request_id": request_id_of(request),
            "method": request.method,
            "path": request.url.path,
        },
    )
    return error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
