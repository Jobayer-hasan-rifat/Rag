"""Response headers that harden every reply, and keep API data out of shared caches."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_HSTS = "max-age=31536000"


class SecurityHeadersMiddleware:
    """Adds `nosniff`, framing and referrer protection to every response.

    API replies also get a CSP that allows nothing, and `Cache-Control: no-store` unless the route
    chose its own (downloads do). The interactive documentation, when enabled, loads scripts from
    a CDN and so keeps only the headers that do not restrict it. HSTS is added when configured,
    for deployments where this process answers over HTTPS (directly or behind a proxy that does
    not add it itself).
    """

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self._hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"] in _DOCS_PATHS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                if self._hsts:
                    headers.setdefault("Strict-Transport-Security", _HSTS)
                if not is_docs:
                    headers.setdefault("Content-Security-Policy", _API_CSP)
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)
