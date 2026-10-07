import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.observability.context import request_id_var

SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "authorization", "api_key", "apikey")
REDACTED = "[REDACTED]"

_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/@\s]*):(?P<pw>[^@\s]+)@"
)
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "request_id", "environment", "taskName"}
)


def redact_text(text: str) -> str:
    return _URL_CREDENTIALS.sub(r"\g<scheme>\g<user>:" + REDACTED + "@", text)


def redact_value(key: str, value: Any) -> Any:
    if any(marker in key.lower() for marker in SENSITIVE_KEYS):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, str):
        return redact_text(value)
    return value


class RequestContextFilter(logging.Filter):
    def __init__(self, environment: str) -> None:
        super().__init__()
        self._environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        record.environment = self._environment
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": getattr(record, "request_id", None),
            "environment": getattr(record, "environment", None),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = redact_value(key, value)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str, environment: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter(environment))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
