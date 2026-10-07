from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.responses import error_response
from app.exceptions import FileTooLargeError


class BodySizeLimitMiddleware:
    """Caps request bodies for selected endpoints before they are buffered to disk.

    Multipart parsing spools uploads to temporary storage, so without a cap a client could
    exhaust disk before application-level validation runs. Checks `Content-Length` up front
    and counts streamed bytes for requests that do not declare one.
    """

    def __init__(
        self, app: ASGIApp, *, rules: dict[tuple[str, str], int], overhead_bytes: int = 0
    ) -> None:
        self.app = app
        self._rules = rules
        self._overhead = overhead_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = (
            self._rules.get((scope["method"], scope["path"])) if scope["type"] == "http" else None
        )
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            error = FileTooLargeError(limit - self._overhead)
            response = error_response(
                Request(scope),
                status_code=error.status_code,
                code=error.code,
                message=error.message,
                details=error.details,
            )
            await response(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise FileTooLargeError(limit - self._overhead)
            return message

        await self.app(scope, counting_receive, send)
