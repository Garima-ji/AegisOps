"""Unit tests for structured logging."""

import json
import logging

from app.core.logging import (
    ConsoleFormatter,
    JSONFormatter,
    set_correlation_id,
)


def test_json_formatter_serializes_valid_json() -> None:
    """Verify JSONFormatter generates valid JSON with mandatory fields."""
    formatter = JSONFormatter(environment="test")
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test log message with %s",
        args=("argument",),
        exc_info=None,
    )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["message"] == "Test log message with argument"
    assert parsed["environment"] == "test"
    assert "timestamp" in parsed


def test_json_formatter_includes_correlation_id() -> None:
    """Verify correlation ID from context is included in log output."""
    formatter = JSONFormatter(environment="test")
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Logged with correlation",
        args=(),
        exc_info=None,
    )

    set_correlation_id("req_abc123")
    try:
        formatted = formatter.format(record)
        parsed = json.loads(formatted)
        assert parsed.get("correlation_id") == "req_abc123"
    finally:
        set_correlation_id(None)


def test_console_formatter_produces_string() -> None:
    """Verify ConsoleFormatter produces human-readable string without error."""
    formatter = ConsoleFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.WARNING,
        pathname="test.py",
        lineno=12,
        msg="Warning message",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    assert "[WARNING ]" in formatted
    assert "Warning message" in formatted
