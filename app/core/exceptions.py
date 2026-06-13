from typing import Any


class CalryException(Exception):
    """Base exception class for Calry backend applications."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: str = "INTERNAL_SERVER_ERROR",
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


class AuthException(CalryException):
    """Exception raised when authentication fails, e.g. token expired or invalid."""

    def __init__(
        self,
        message: str = "Authentication failed",
        error_code: str = "UNAUTHORIZED",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=401,
            error_code=error_code,
            details=details,
        )


class ForbiddenException(CalryException):
    """Exception raised when access to a resource is forbidden."""

    def __init__(
        self,
        message: str = "Access forbidden",
        error_code: str = "FORBIDDEN",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=403,
            error_code=error_code,
            details=details,
        )


class NotFoundException(CalryException):
    """Exception raised when a requested resource is not found."""

    def __init__(
        self,
        message: str = "Resource not found",
        error_code: str = "NOT_FOUND",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=404,
            error_code=error_code,
            details=details,
        )


class ValidationException(CalryException):
    """Exception raised when input payload fails business validation constraints."""

    def __init__(
        self,
        message: str = "Validation failed",
        error_code: str = "VALIDATION_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=422,
            error_code=error_code,
            details=details,
        )


class AIProviderException(CalryException):
    """Exception raised when downstream AI APIs or estimation services fail."""

    def __init__(
        self,
        message: str = "AI Estimation failed",
        error_code: str = "AI_PROVIDER_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=502,
            error_code=error_code,
            details=details,
        )


class DatabaseException(CalryException):
    """Exception raised for unexpected database operation failures."""

    def __init__(
        self,
        message: str = "Database operation failed",
        error_code: str = "DATABASE_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=500,
            error_code=error_code,
            details=details,
        )
