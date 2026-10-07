import pytest
from starlette.types import Message, Receive, Scope, Send

from app.api.middleware.body_limit import BodySizeLimitMiddleware
from app.exceptions import FileTooLargeError

LIMIT = 100


def _scope(
    method: str = "POST", path: str = "/up", headers: list[tuple[bytes, bytes]] | None = None
) -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "state": {},
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("c", 1),
        "http_version": "1.1",
        "raw_path": path.encode(),
    }


class Recorder:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent: list[Message] = []
        self.app_called = False

    async def receive(self) -> Message:
        chunk = self._chunks.pop(0) if self._chunks else b""
        return {"type": "http.request", "body": chunk, "more_body": bool(self._chunks)}

    async def send(self, message: Message) -> None:
        self.sent.append(message)


async def _reading_app(scope: Scope, receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _middleware() -> BodySizeLimitMiddleware:
    return BodySizeLimitMiddleware(_reading_app, rules={("POST", "/up"): LIMIT}, overhead_bytes=20)


async def test_declared_length_over_the_limit_is_rejected_without_running_the_app() -> None:
    recorder = Recorder([])
    scope = _scope(headers=[(b"content-length", b"101")])

    await _middleware()(scope, recorder.receive, recorder.send)

    assert recorder.sent[0]["status"] == 413


async def test_streamed_bytes_over_the_limit_abort_the_request() -> None:
    recorder = Recorder([b"a" * 60, b"b" * 60])

    with pytest.raises(FileTooLargeError) as error:
        await _middleware()(_scope(), recorder.receive, recorder.send)

    assert error.value.details == {
        "max_bytes": LIMIT - 20
    }  # the configured cap, not the padded one


async def test_bodies_within_the_limit_pass_through() -> None:
    recorder = Recorder([b"a" * 50, b"b" * 50])

    await _middleware()(_scope(), recorder.receive, recorder.send)

    assert recorder.sent[0]["status"] == 200


@pytest.mark.parametrize(("method", "path"), [("GET", "/up"), ("POST", "/other"), ("PUT", "/up")])
async def test_other_routes_are_not_limited(method: str, path: str) -> None:
    recorder = Recorder([b"a" * 500])

    await _middleware()(_scope(method, path), recorder.receive, recorder.send)

    assert recorder.sent[0]["status"] == 200


async def test_non_http_scopes_pass_through() -> None:
    called: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        called.append(scope["type"])

    middleware = BodySizeLimitMiddleware(app, rules={("POST", "/up"): LIMIT})

    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]

    assert called == ["lifespan"]
