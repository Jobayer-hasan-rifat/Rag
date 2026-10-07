import json
import logging
from collections.abc import Iterator

import pytest

from app.observability.context import request_id_var
from app.observability.logging import REDACTED, JsonFormatter, RequestContextFilter, redact_text


@pytest.fixture
def logger_output() -> Iterator[tuple[logging.Logger, list[str]]]:
    lines: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = ListHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter("test"))
    logger = logging.getLogger("tests.logging")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, lines
    logger.handlers = []


def test_log_line_is_json_with_required_fields(
    logger_output: tuple[logging.Logger, list[str]],
) -> None:
    logger, lines = logger_output

    logger.info("hello", extra={"duration_ms": 12.5})

    entry = json.loads(lines[0])
    assert entry["level"] == "INFO"
    assert entry["logger"] == "tests.logging"
    assert entry["message"] == "hello"
    assert entry["environment"] == "test"
    assert entry["duration_ms"] == 12.5
    assert entry["timestamp"].endswith("+00:00")
    assert "request_id" in entry


def test_log_line_carries_current_request_id(
    logger_output: tuple[logging.Logger, list[str]],
) -> None:
    logger, lines = logger_output
    token = request_id_var.set("req-12345678")
    try:
        logger.info("inside request")
    finally:
        request_id_var.reset(token)

    assert json.loads(lines[0])["request_id"] == "req-12345678"


def test_sensitive_extra_fields_are_redacted(
    logger_output: tuple[logging.Logger, list[str]],
) -> None:
    logger, lines = logger_output

    logger.info(
        "login attempt",
        extra={
            "password": "hunter2",
            "access_token": "abc",
            "headers": {"Authorization": "Bearer x"},
        },
    )

    raw = lines[0]
    entry = json.loads(raw)
    assert entry["password"] == REDACTED
    assert entry["access_token"] == REDACTED
    assert entry["headers"]["Authorization"] == REDACTED
    assert "hunter2" not in raw
    assert "Bearer x" not in raw


def test_credentials_in_urls_are_scrubbed_from_messages() -> None:
    text = redact_text("connect failed: postgresql+asyncpg://rag_user:s3cret@db:5432/rag")
    assert "s3cret" not in text
    assert "rag_user" in text


def test_exception_traceback_is_logged_without_url_credentials(
    logger_output: tuple[logging.Logger, list[str]],
) -> None:
    logger, lines = logger_output

    try:
        raise ConnectionError("cannot reach redis://:topsecret@redis:6379/0")
    except ConnectionError:
        logger.error("boom", exc_info=True)

    entry = json.loads(lines[0])
    assert "ConnectionError" in entry["exception"]
    assert "topsecret" not in lines[0]
