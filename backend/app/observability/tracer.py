"""Unified distributed pipeline tracer for end-to-end incident lifecycle observability."""

from __future__ import annotations

import contextvars
import logging
import threading
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_correlation_id, set_correlation_id

logger = logging.getLogger("aegisops.observability.tracer")

# Context variable tracking current active span
_current_span_ctx: contextvars.ContextVar[TraceSpan | None] = contextvars.ContextVar(
    "current_span", default=None
)


@dataclass
class TraceSpan:
    """Represents a bounded operational span within the pipeline."""

    span_id: str
    trace_id: str
    name: str
    parent_span_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    duration_ms: float = 0.0
    status: str = "ok"  # ok | error | degraded
    tags: dict[str, Any] = field(default_factory=dict)
    error_category: str | None = None
    error_message: str | None = None

    def finish(
        self,
        status: str = "ok",
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Mark span as finished and compute duration."""
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        self.status = status
        if error_category:
            self.error_category = error_category
        if error_message:
            # Bound error message length to prevent memory leakage
            self.error_message = error_message[:500]

    def set_tag(self, key: str, value: Any) -> None:
        """Set a bounded metadata tag on the span."""
        # Sanitize sensitive keys
        sensitive_keys = {"password", "secret", "token", "key", "authorization", "prompt"}
        if any(s in key.lower() for s in sensitive_keys):
            return
        if isinstance(value, str):
            self.tags[key] = value[:200]
        elif isinstance(value, (int, float, bool)):
            self.tags[key] = value
        else:
            self.tags[key] = str(value)[:200]

    def to_dict(self) -> dict[str, Any]:
        """Convert span to dictionary."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "parent_span_id": self.parent_span_id,
            "start_time": datetime.fromtimestamp(self.start_time, tz=UTC).isoformat(),
            "end_time": (
                datetime.fromtimestamp(self.end_time, tz=UTC).isoformat() if self.end_time else None
            ),
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_category": self.error_category,
            "error_message": self.error_message,
            "tags": self.tags,
        }


class PipelineTracer:
    """Thread-safe operational tracer capturing pipeline lifecycle execution."""

    def __init__(self, max_retained_spans: int = 1000) -> None:
        self._lock = threading.RLock()
        self._max_retained = max_retained_spans
        self._completed_spans: list[TraceSpan] = []

    def get_current_span(self) -> TraceSpan | None:
        """Get currently active span from async context."""
        return _current_span_ctx.get()

    @contextmanager
    def span(
        self,
        name: str,
        tags: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> Generator[TraceSpan, None, None]:
        """Context manager creating a traced operational span."""
        parent = self.get_current_span()
        corr_id = get_correlation_id()

        # Derive trace ID: explicit -> parent -> correlation_id -> new uuid
        effective_trace_id = (
            trace_id
            or (parent.trace_id if parent else None)
            or corr_id
            or f"tr_{uuid.uuid4().hex[:16]}"
        )

        span_id = f"sp_{uuid.uuid4().hex[:12]}"
        parent_id = parent.span_id if parent else None

        span = TraceSpan(
            span_id=span_id,
            trace_id=effective_trace_id,
            name=name,
            parent_span_id=parent_id,
        )

        if tags:
            for k, v in tags.items():
                span.set_tag(k, v)

        # Set span as current in context
        token = _current_span_ctx.set(span)
        # Ensure correlation ID context is aligned
        corr_token = None
        if not corr_id:
            corr_token = set_correlation_id(effective_trace_id)

        try:
            yield span
            if span.end_time is None:
                span.finish(status="ok")
        except Exception as exc:
            span.finish(
                status="error",
                error_category=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        finally:
            _current_span_ctx.reset(token)
            if corr_token:
                set_correlation_id(None)
            self._record_completed_span(span)

    def _record_completed_span(self, span: TraceSpan) -> None:
        """Store span in circular memory buffer."""
        with self._lock:
            self._completed_spans.append(span)
            if len(self._completed_spans) > self._max_retained:
                self._completed_spans = self._completed_spans[-self._max_retained :]

    def get_spans_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Retrieve all recorded spans belonging to a given trace ID."""
        with self._lock:
            return [s.to_dict() for s in self._completed_spans if s.trace_id == trace_id]

    def get_recent_spans(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve recent completed spans."""
        with self._lock:
            return [s.to_dict() for s in self._completed_spans[-limit:]]

    def reset(self) -> None:
        """Reset tracer state for testing isolation."""
        with self._lock:
            self._completed_spans.clear()


# Global pipeline tracer singleton
tracer = PipelineTracer()
