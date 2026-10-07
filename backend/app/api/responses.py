from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorBody, ErrorResponse, Meta, ResponseEnvelope

REQUEST_ID_HEADER = "X-Request-ID"


def request_id_of(request: Request) -> str | None:
    request_id: str | None = getattr(request.state, "request_id", None)
    return request_id


def build_meta(request: Request) -> Meta:
    return Meta(request_id=request_id_of(request), timestamp=datetime.now(UTC))


def envelope(request: Request, data: Any) -> ResponseEnvelope[Any]:
    return ResponseEnvelope[Any](data=data, meta=build_meta(request))


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details or {}),
        meta=build_meta(request),
    )
    response_headers = dict(headers or {})
    request_id = request_id_of(request)
    if request_id:
        response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code, content=jsonable_encoder(body), headers=response_headers
    )
