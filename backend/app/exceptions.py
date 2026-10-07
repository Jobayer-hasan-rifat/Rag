from typing import Any


class AppError(Exception):
    """Base class for expected, client-facing application errors."""

    status_code: int = 400
    code: str = "APPLICATION_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
