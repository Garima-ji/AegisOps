"""Typed Pydantic schemas for metrics, logs, traces, and batch ingestion."""

import math
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SERVICE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{2,64}$")
TRACE_ID_REGEX = re.compile(r"^[a-fA-F0-9]{32}$")
SPAN_ID_REGEX = re.compile(r"^[a-fA-F0-9]{16}$")

MAX_ATTRIBUTES_COUNT = 32
MAX_ATTR_KEY_LENGTH = 64
MAX_ATTR_VAL_LENGTH = 512
MAX_LOG_MESSAGE_LENGTH = 4096


def sanitize_attributes(attrs: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize and bound key-value attribute maps to prevent memory exhaustion."""
    if not attrs:
        return {}
    sanitized: dict[str, Any] = {}
    for i, (k, v) in enumerate(attrs.items()):
        if i >= MAX_ATTRIBUTES_COUNT:
            break
        str_k = str(k)[:MAX_ATTR_KEY_LENGTH].strip()
        if not str_k:
            continue
        if isinstance(v, (int, float, bool)):
            sanitized[str_k] = v
        elif isinstance(v, str):
            sanitized[str_k] = v[:MAX_ATTR_VAL_LENGTH]
        elif v is None:
            sanitized[str_k] = None
        else:
            sanitized[str_k] = str(v)[:MAX_ATTR_VAL_LENGTH]
    return sanitized


class MetricPoint(BaseModel):
    """Schema for individual numeric time-series metric observations."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(
        description="UTC timestamp of the observation",
    )
    service_name: str = Field(
        description="Originating service identifier",
        min_length=2,
        max_length=64,
    )
    environment: str = Field(
        default="production",
        description="Deployment environment (production, staging, development, test)",
        max_length=32,
    )
    metric_name: str = Field(
        description="Standardized dot/underscore metric identifier",
        min_length=2,
        max_length=128,
    )
    value: float = Field(
        description="Finite numeric observation value",
    )
    unit: str = Field(
        default="count",
        description="Standard unit (ms, count, percent, bytes, ratio)",
        max_length=32,
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded contextual key-value dimensions",
    )
    trace_id: str | None = Field(
        default=None,
        description="Optional 32-hex character trace correlation ID",
    )
    ingested_at: datetime | None = Field(
        default=None,
        description="Server timestamp when recorded",
    )

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        s = v.strip()
        if not SERVICE_NAME_REGEX.match(s):
            raise ValueError(
                f"Invalid service_name '{v}'. Must be 2-64 characters matching [a-zA-Z0-9_.-]."
            )
        return s

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("Metric value must be a finite number (not NaN or Inf).")
        return v

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=UTC)
            return v.astimezone(UTC)
        if isinstance(v, (int, float)):
            # Epoch seconds or milliseconds
            if v > 1e11:  # Likely milliseconds
                v = v / 1000.0
            return datetime.fromtimestamp(v, tz=UTC)
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.astimezone(UTC)
        raise ValueError("Invalid timestamp format.")

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip().lower()
            if not TRACE_ID_REGEX.match(stripped):
                raise ValueError("trace_id must be a 32-character hexadecimal string.")
            return stripped
        return None

    @field_validator("attributes", mode="before")
    @classmethod
    def sanitize_attrs(cls, v: Any) -> dict[str, Any]:
        if isinstance(v, dict):
            return sanitize_attributes(v)
        return {}


class MetricBatchRequest(BaseModel):
    """Batch ingestion payload for metrics."""

    model_config = ConfigDict(extra="forbid")
    items: list[MetricPoint] = Field(min_length=1, max_length=1000)


class LogRecordSchema(BaseModel):
    """Schema for structured log events."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(description="UTC timestamp when log event occurred")
    service_name: str = Field(min_length=2, max_length=64)
    environment: str = Field(default="production", max_length=32)
    severity: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Standard log severity level"
    )
    message: str = Field(max_length=MAX_LOG_MESSAGE_LENGTH)
    attributes: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None)
    span_id: str | None = Field(default=None)
    exception_type: str | None = Field(default=None, max_length=128)
    error_class: str | None = Field(default=None, max_length=64)
    ingested_at: datetime | None = Field(default=None)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        s = v.strip()
        if not SERVICE_NAME_REGEX.match(s):
            raise ValueError(f"Invalid service_name '{v}'.")
        return s

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, v: Any) -> datetime:
        return MetricPoint.normalize_timestamp(v)

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        # Prevent binary/null bytes and truncate
        cleaned = v.replace("\x00", "").strip()
        return cleaned[:MAX_LOG_MESSAGE_LENGTH]

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, v: str | None) -> str | None:
        return MetricPoint.validate_trace_id(v)

    @field_validator("span_id")
    @classmethod
    def validate_span_id(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip().lower()
            if not SPAN_ID_REGEX.match(stripped):
                raise ValueError("span_id must be a 16-character hexadecimal string.")
            return stripped
        return None

    @field_validator("attributes", mode="before")
    @classmethod
    def sanitize_attrs(cls, v: Any) -> dict[str, Any]:
        if isinstance(v, dict):
            return sanitize_attributes(v)
        return {}


class LogBatchRequest(BaseModel):
    """Batch ingestion payload for structured logs."""

    model_config = ConfigDict(extra="forbid")
    items: list[LogRecordSchema] = Field(min_length=1, max_length=1000)


class SpanRecordSchema(BaseModel):
    """Schema for OpenTelemetry-compatible distributed trace span events."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(description="32-character hexadecimal trace ID")
    span_id: str = Field(description="16-character hexadecimal span ID")
    parent_span_id: str | None = Field(
        default=None, description="16-hex parent span ID or None for root"
    )
    service_name: str = Field(min_length=2, max_length=64)
    operation_name: str = Field(min_length=1, max_length=128)
    start_time: datetime = Field(description="Span commencement UTC timestamp")
    end_time: datetime = Field(description="Span termination UTC timestamp")
    duration_ms: float | None = Field(
        default=None, ge=0.0, description="Span duration in milliseconds"
    )
    status_code: Literal["OK", "ERROR", "UNSET"] = Field(default="UNSET")
    status_message: str | None = Field(default=None, max_length=512)
    attributes: dict[str, Any] = Field(default_factory=dict)
    ingested_at: datetime | None = Field(default=None)

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, v: str) -> str:
        s = v.strip().lower()
        if not TRACE_ID_REGEX.match(s):
            raise ValueError("trace_id must be a 32-character hexadecimal string.")
        return s

    @field_validator("span_id")
    @classmethod
    def validate_span_id(cls, v: str) -> str:
        s = v.strip().lower()
        if not SPAN_ID_REGEX.match(s):
            raise ValueError("span_id must be a 16-character hexadecimal string.")
        return s

    @field_validator("parent_span_id")
    @classmethod
    def validate_parent_span_id(cls, v: str | None) -> str | None:
        if v is not None:
            s = v.strip().lower()
            if not SPAN_ID_REGEX.match(s):
                raise ValueError("parent_span_id must be a 16-character hexadecimal string.")
            return s
        return None

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        return MetricPoint.validate_service_name(v)

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def normalize_timestamp(cls, v: Any) -> datetime:
        return MetricPoint.normalize_timestamp(v)

    @field_validator("attributes", mode="before")
    @classmethod
    def sanitize_attrs(cls, v: Any) -> dict[str, Any]:
        if isinstance(v, dict):
            return sanitize_attributes(v)
        return {}

    @model_validator(mode="after")
    def validate_chronology_and_duration(self) -> "SpanRecordSchema":
        if self.end_time < self.start_time:
            raise ValueError("end_time cannot be earlier than start_time.")
        computed_ms = round((self.end_time - self.start_time).total_seconds() * 1000, 3)
        if self.duration_ms is None:
            self.duration_ms = computed_ms
        return self


class TraceBatchRequest(BaseModel):
    """Batch ingestion payload for trace spans."""

    model_config = ConfigDict(extra="forbid")
    items: list[SpanRecordSchema] = Field(min_length=1, max_length=1000)


class IngestionItemError(BaseModel):
    """Detailed error for an invalid record within an ingestion batch."""

    model_config = ConfigDict(extra="forbid")
    index: int
    field: str
    reason: str


class IngestionResponse(BaseModel):
    """Result summary of batch ingestion."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "partial_success", "failed"]
    received: int
    accepted: int
    rejected: int
    errors: list[IngestionItemError] = Field(default_factory=list)
    duration_ms: float


class MetricListResponse(BaseModel):
    """Paginated list of metric observations."""

    model_config = ConfigDict(extra="forbid")
    items: list[MetricPoint]
    total: int
    limit: int
    offset: int


class LogListResponse(BaseModel):
    """Paginated list of structured log records."""

    model_config = ConfigDict(extra="forbid")
    items: list[LogRecordSchema]
    total: int
    limit: int
    offset: int


class TraceListResponse(BaseModel):
    """Paginated list of trace spans."""

    model_config = ConfigDict(extra="forbid")
    items: list[SpanRecordSchema]
    total: int
    limit: int
    offset: int


class TraceWaterfallResponse(BaseModel):
    """Hierarchical waterfall span collection for a single distributed trace."""

    model_config = ConfigDict(extra="forbid")
    trace_id: str
    span_count: int
    spans: list[SpanRecordSchema]


class PipelineMetricsResponse(BaseModel):
    """Operational telemetry counters and latency summaries."""

    model_config = ConfigDict(extra="forbid")
    counters: dict[str, int]
    durations_ms: dict[str, Any]
    active_experiments: int
