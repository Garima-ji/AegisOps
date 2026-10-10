"""Unit tests for configuration loading and validation."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_default_settings() -> None:
    """Verify default settings instantiation."""
    settings = Settings()
    assert settings.app_name == "AegisOps"
    assert settings.app_version == "0.1.0"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.port == 8000
    assert "http://localhost:3000" in settings.allowed_origins


def test_allowed_origins_parsing_comma_separated() -> None:
    """Verify comma-separated string parsing for allowed origins."""
    settings = Settings(
        allowed_origins="http://example.com, https://app.example.com"  # type: ignore[arg-type]
    )
    assert settings.allowed_origins == ["http://example.com", "https://app.example.com"]


def test_allowed_origins_empty_string() -> None:
    """Verify empty string yields empty list."""
    settings = Settings(allowed_origins="")  # type: ignore[arg-type]
    assert settings.allowed_origins == []


def test_invalid_database_scheme() -> None:
    """Verify unsupported database scheme raises validation error."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(database_url="mysql+pymysql://user:pass@localhost/db")
    assert "Unsupported database scheme" in str(exc_info.value)


def test_short_secret_key_rejection() -> None:
    """Verify short secret key is rejected by validator."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(secret_key="short")  # type: ignore[arg-type]
    assert "SECRET_KEY must be at least 16 characters" in str(exc_info.value)


def test_environment_helper_properties() -> None:
    """Verify environment helper predicates."""
    dev_settings = Settings(environment="development")
    assert dev_settings.is_development is True
    assert dev_settings.is_production is False
    assert dev_settings.is_testing is False

    prod_settings = Settings(environment="production")
    assert prod_settings.is_production is True
    assert prod_settings.is_development is False

    test_settings = Settings(environment="test")
    assert test_settings.is_testing is True
