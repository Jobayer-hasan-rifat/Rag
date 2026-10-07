from typing import Any


class AppError(Exception):
    """Base class for expected, client-facing application errors."""

    status_code: int = 400
    code: str = "APPLICATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


class AuthenticationError(AppError):
    status_code = 401
    code = "AUTHENTICATION_ERROR"

    def __init__(self, message: str = "Invalid or expired credentials") -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class AuthorizationError(AppError):
    status_code = 403
    code = "AUTHORIZATION_ERROR"

    def __init__(self, message: str = "You do not have permission to perform this action") -> None:
        super().__init__(message)


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class RateLimitExceededError(AppError):
    status_code = 429
    code = "RATE_LIMIT_EXCEEDED"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after_seconds)},
        )


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"

    def __init__(self, message: str = "Service temporarily unavailable") -> None:
        super().__init__(message)
