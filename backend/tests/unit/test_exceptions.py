"""Unit tests for domain exceptions."""

from app.core.exceptions import (
    AegisOpsError,
    ConfigurationError,
    DatabaseConnectionError,
    PolicyViolationError,
    ResourceNotFoundError,
    ValidationError,
)


def test_base_aegis_ops_error() -> None:
    """Verify base exception attributes."""
    err = AegisOpsError("Something failed", code="CUSTOM_CODE", status_code=400)
    assert err.message == "Something failed"
    assert err.code == "CUSTOM_CODE"
    assert err.status_code == 400


def test_specialized_exceptions() -> None:
    """Verify default status codes and error codes for specialized errors."""
    config_err = ConfigurationError("Bad config")
    assert config_err.code == "CONFIGURATION_ERROR"
    assert config_err.status_code == 500

    db_err = DatabaseConnectionError("DB is down")
    assert db_err.code == "DATABASE_UNAVAILABLE"
    assert db_err.status_code == 503

    not_found = ResourceNotFoundError("Missing metric")
    assert not_found.code == "RESOURCE_NOT_FOUND"
    assert not_found.status_code == 404

    val_err = ValidationError("Bad input payload")
    assert val_err.code == "VALIDATION_ERROR"
    assert val_err.status_code == 422

    policy_err = PolicyViolationError("Forbidden remediation action")
    assert policy_err.code == "POLICY_VIOLATION"
    assert policy_err.status_code == 403
