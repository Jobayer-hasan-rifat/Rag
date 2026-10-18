import re
from collections.abc import Callable

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.responses import error_response
from app.exceptions import AppError, FileTooLargeError


def _compile_path(path: str) -> re.Pattern[str]:
    segments = ["[^/]+" if s.startswith("{") else re.escape(s) for s in path.split("/")]
    return re.compile("^" + "/".join(segments) + "$")


class BodySizeLimitMiddleware:
    """Caps request bodies for selected endpoints before they are buffered to disk.

    Multipart parsing spools uploads to temporary storage, so without a cap a client could
    exhaust disk before application-level validation runs. Checks `Content-Length` up front
    and counts streamed bytes for requests that do not declare one.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        rules: dict[tuple[str, str], int],
        overhead_bytes: int = 0,
        error_factory: Callable[[int], AppError] = FileTooLargeError,
        default_limit: int | None = None,
        skip: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        """`default_limit` caps every other request that carries a body (POST, PUT, PATCH),
        except the routes named in `skip`, which another instance of this middleware handles."""
        self.app = app
        self._rules = {k: v for k, v in rules.items() if "{" not in k[1]}
        # a `{name}` segment in a rule path matches any single path segment
        self._patterns = [
            (method, _compile_path(path), limit)
            for (method, path), limit in rules.items()
            if "{" in path
        ]
        self._overhead = overhead_bytes
        self._error_factory = error_factory
        self._default = default_limit
        self._skip_exact = {k for k in skip if "{" not in k[1]}
        self._skip_patterns = [
            (method, _compile_path(path)) for (method, path) in skip if "{" in path
        ]

    def _limit_for(self, scope: Scope) -> int | None:
        exact = self._rules.get((scope["method"], scope["path"]))
        if exact is not None:
            return exact
        for method, pattern, limit in self._patterns:
            if scope["method"] == method and pattern.match(scope["path"]):
                return limit
        if self._default is not None and scope["method"] in ("POST", "PUT", "PATCH"):
            if (scope["method"], scope["path"]) in self._skip_exact:
                return None
            if any(
                m == scope["method"] and pat.match(scope["path"]) for m, pat in self._skip_patterns
            ):
                return None
            return self._default
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self._limit_for(scope) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            error = self._error_factory(limit - self._overhead)
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
                    raise self._error_factory(limit - self._overhead)
            return message

        await self.app(scope, counting_receive, send)
