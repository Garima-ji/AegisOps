"""Domain-specific exception hierarchy and RFC 7807 error structures."""

from typing import Any


class AegisOpsError(Exception):
    """Base exception for all domain-level AegisOps application errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: dict[str, Any] | list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class ConfigurationError(AegisOpsError):
    """Raised when environment or startup configuration is invalid."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="CONFIGURATION_ERROR",
            status_code=500,
            details=details,
        )


class DatabaseConnectionError(AegisOpsError):
    """Raised when database connection fails or pool is exhausted."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="DATABASE_UNAVAILABLE",
            status_code=503,
            details=details,
        )


class ResourceNotFoundError(AegisOpsError):
    """Raised when a requested resource is not found."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="RESOURCE_NOT_FOUND",
            status_code=404,
            details=details,
        )


class ValidationError(AegisOpsError):
    """Raised when business logic validation fails."""

    def __init__(self, message: str, details: dict[str, Any] | list[Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=422,
            details=details,
        )


class PolicyViolationError(AegisOpsError):
    """Raised when an action violates safety or remediation policy rules."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="POLICY_VIOLATION",
            status_code=403,
            details=details,
        )


class ServiceUnavailableError(AegisOpsError):
    """Raised when an internal or external dependency is unavailable."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="SERVICE_UNAVAILABLE",
            status_code=503,
            details=details,
        )


class ModelUnavailableError(AegisOpsError):
    """Raised when an ML model is not trained, not calibrated, or unavailable."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="MODEL_UNAVAILABLE",
            status_code=503,
            details=details,
        )
