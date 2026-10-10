"""Structured logging configuration for AegisOps."""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings

# Context variable for distributed request tracing / correlation ID
correlation_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> str | None:
    """Retrieve current request correlation ID from context."""
    return correlation_id_ctx.get()


def set_correlation_id(correlation_id: str | None) -> contextvars.Token[str | None]:
    """Set correlation ID in current async context."""
    return correlation_id_ctx.set(correlation_id)


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def __init__(self, environment: str = "development") -> None:
        super().__init__()
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        """Serialize log record into structured JSON string."""
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "environment": self.environment,
        }

        # Include request correlation ID if available
        req_id = get_correlation_id()
        if req_id:
            log_entry["correlation_id"] = req_id

        # Include exception traceback if present
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include custom extra attributes
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in standard_attrs}
        if extras:
            log_entry["extra"] = extras

        return json.dumps(log_entry, default=str)


class ConsoleFormatter(logging.Formatter):
    """Format log records for local human-readable terminal output."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        req_id = get_correlation_id()
        cid_str = f" [{req_id}]" if req_id else ""
        base = (
            f"[{timestamp}] [{record.levelname:<8}] [{record.name}]{cid_str} {record.getMessage()}"
        )
        if record.exc_info and record.exc_info[0]:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(settings: Settings | None = None) -> None:
    """Initialize structured logging based on application settings."""
    if settings is None:
        settings = get_settings()

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers to avoid duplicate log entries
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        stream_handler.setFormatter(JSONFormatter(environment=settings.environment))
    else:
        stream_handler.setFormatter(ConsoleFormatter())

    root_logger.addHandler(stream_handler)

    # Moderate noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    if not settings.db_echo:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience getter for namespaced application logger."""
    return logging.getLogger(name)
