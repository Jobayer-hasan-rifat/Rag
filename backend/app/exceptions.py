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


class NotFoundError(AppError):
    """Also used when a resource exists but belongs to someone else, to avoid leaking existence."""

    status_code = 404
    code = "NOT_FOUND"

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message)


class FileTooLargeError(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"

    def __init__(self, max_bytes: int) -> None:
        super().__init__("File exceeds the maximum allowed size", details={"max_bytes": max_bytes})


class UnsupportedFileTypeError(AppError):
    status_code = 415
    code = "UNSUPPORTED_FILE_TYPE"

    def __init__(self, message: str = "Unsupported file type") -> None:
        super().__init__(message)


class InvalidFileError(AppError):
    status_code = 422
    code = "INVALID_FILE"


class DuplicateDocumentError(ConflictError):
    code = "DUPLICATE_DOCUMENT"

    def __init__(self, existing_document_id: str) -> None:
        super().__init__(
            "An identical document already exists",
            details={"existing_document_id": existing_document_id},
        )


class InvalidStatusTransitionError(ConflictError):
    code = "INVALID_STATE_TRANSITION"


class StorageUnavailableError(AppError):
    status_code = 503
    code = "STORAGE_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__("File storage is temporarily unavailable")


class ContentUnavailableError(AppError):
    """The record exists but its stored object does not (an operator-level inconsistency)."""

    status_code = 500
    code = "STORAGE_INCONSISTENCY"

    def __init__(self) -> None:
        super().__init__("Document content is currently unavailable")


class RequestValidationFailedError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class QuotaExceededError(AppError):
    status_code = 403
    code = "QUOTA_EXCEEDED"

    def __init__(self, quota_bytes: int) -> None:
        super().__init__(
            "Your storage quota would be exceeded; delete documents to free space",
            details={"quota_bytes": quota_bytes},
        )
