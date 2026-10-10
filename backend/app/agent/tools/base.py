"""Base classes and execution auditing for typed bounded investigation tools."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

# Hard bounds applied across all tools to prevent excessive retrieval and memory blowup
MAX_TELEMETRY_ITEMS = 50
MAX_WINDOW_HOURS = 4
MAX_LOG_QUERY_LEN = 120


class ToolAuditEntry(BaseModel):
    """Audit log entry capturing tool invocation, arguments, and performance."""

    tool_name: str
    arguments: dict[str, Any]
    success: bool
    duration_ms: float
    record_count: int = 0
    error_category: str | None = None
    error_message: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolExecutionResponse(BaseModel):
    """Standardized structured envelope for all tool results."""

    success: bool
    data: Any = None
    error: str | None = None
    error_category: str | None = None
    audit: ToolAuditEntry


class BaseInvestigationTool(ABC):
    """Abstract base for all bounded investigation tools."""

    name: str
    description: str

    def execute_with_audit(self, **kwargs: Any) -> ToolExecutionResponse:
        """Execute the tool with safety boundaries, validation, and structured timing audit."""
        start_t = time.perf_counter()
        sanitized_args = {k: v for k, v in kwargs.items() if k not in ("db", "session")}

        try:
            result_data, record_count = self._run(**kwargs)
            duration = (time.perf_counter() - start_t) * 1000.0

            audit = ToolAuditEntry(
                tool_name=self.name,
                arguments=sanitized_args,
                success=True,
                duration_ms=round(duration, 2),
                record_count=record_count,
            )
            return ToolExecutionResponse(
                success=True,
                data=result_data,
                audit=audit,
            )

        except ValueError as val_err:
            duration = (time.perf_counter() - start_t) * 1000.0
            audit = ToolAuditEntry(
                tool_name=self.name,
                arguments=sanitized_args,
                success=False,
                duration_ms=round(duration, 2),
                error_category="INVALID_ARGUMENTS",
                error_message=str(val_err),
            )
            return ToolExecutionResponse(
                success=False,
                error=str(val_err),
                error_category="INVALID_ARGUMENTS",
                audit=audit,
            )

        except TimeoutError as to_err:
            duration = (time.perf_counter() - start_t) * 1000.0
            audit = ToolAuditEntry(
                tool_name=self.name,
                arguments=sanitized_args,
                success=False,
                duration_ms=round(duration, 2),
                error_category="TIMEOUT",
                error_message=str(to_err),
            )
            return ToolExecutionResponse(
                success=False,
                error=str(to_err),
                error_category="TIMEOUT",
                audit=audit,
            )

        except Exception as exc:
            duration = (time.perf_counter() - start_t) * 1000.0
            audit = ToolAuditEntry(
                tool_name=self.name,
                arguments=sanitized_args,
                success=False,
                duration_ms=round(duration, 2),
                error_category="EXECUTION_ERROR",
                error_message=str(exc),
            )
            return ToolExecutionResponse(
                success=False,
                error=str(exc),
                error_category="EXECUTION_ERROR",
                audit=audit,
            )

    @abstractmethod
    def _run(self, **kwargs: Any) -> tuple[Any, int]:
        """Internal execution returning (result_payload, record_count)."""
        pass
