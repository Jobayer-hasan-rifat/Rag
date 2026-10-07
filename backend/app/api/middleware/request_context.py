import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.context import request_id_var
from app.observability.logging import get_logger

_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
_QUIET_PATH_PREFIXES = ("/health", "/api/v1/health")

logger = get_logger("app.request")


def resolve_request_id(candidate: str | None) -> str:
    """Keep a well-formed client/proxy ID for correlation; replace anything else."""
    if candidate and _VALID_REQUEST_ID.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


class RequestContextMiddleware:
    """Assigns a request ID, exposes it to logs and the response, and logs completion."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope).get("x-request-id"))
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            quiet = scope["path"].startswith(_QUIET_PATH_PREFIXES)
            logger.log(
                logging.DEBUG if quiet else logging.INFO,
                "request completed",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_var.reset(token)
