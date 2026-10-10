"""Unit tests for Pydantic v2 telemetry and experiment schemas."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.experiment import ExperimentCreateRequest
from app.schemas.telemetry import (
    LogBatchRequest,
    LogRecordSchema,
    MetricBatchRequest,
    MetricPoint,
    SpanRecordSchema,
    TraceBatchRequest,
)


def test_metric_point_valid() -> None:
    """Test valid metric point schema initialization and UTC normalization."""
    now = datetime.now(UTC)
    mp = MetricPoint(
        timestamp=now,
        service_name="payment-service",
        environment="production",
        metric_name="http.server.request.duration_ms",
        value=142.5,
        unit="ms",
        attributes={"endpoint": "/charge", "status": "200"},
        trace_id="a" * 32,
    )
    assert mp.service_name == "payment-service"
    assert mp.value == 142.5
    assert mp.trace_id == "a" * 32
    assert mp.timestamp.tzinfo is not None


def test_metric_point_timestamp_conversion() -> None:
    """Test parsing timestamps from epoch floats and ISO strings."""
    # From epoch
    mp1 = MetricPoint(
        timestamp=1700000000.0,
        service_name="order-service",
        metric_name="requests.count",
        value=1.0,
    )
    assert mp1.timestamp.year >= 2023
    assert mp1.timestamp.tzinfo == UTC

    # From ISO string
    mp2 = MetricPoint(
        timestamp="2026-10-10T12:00:00Z",
        service_name="order-service",
        metric_name="requests.count",
        value=1.0,
    )
    assert mp2.timestamp == datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)


def test_metric_point_invalid_service_name() -> None:
    """Service names with invalid characters or bad lengths must be rejected."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        MetricPoint(
            timestamp=now,
            service_name="s",  # too short (< 2)
            metric_name="test.metric",
            value=1.0,
        )

    with pytest.raises(ValidationError):
        MetricPoint(
            timestamp=now,
            service_name="bad service with spaces!",
            metric_name="test.metric",
            value=1.0,
        )


def test_metric_point_rejects_nan_and_inf() -> None:
    """Metric values must be finite numbers."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        MetricPoint(
            timestamp=now,
            service_name="order-service",
            metric_name="test.metric",
            value=float("nan"),
        )
    with pytest.raises(ValidationError):
        MetricPoint(
            timestamp=now,
            service_name="order-service",
            metric_name="test.metric",
            value=float("inf"),
        )


def test_metric_point_invalid_trace_id() -> None:
    """trace_id must be exactly 32 hexadecimal characters."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        MetricPoint(
            timestamp=now,
            service_name="order-service",
            metric_name="test.metric",
            value=1.0,
            trace_id="invalid-trace-id",
        )


def test_attribute_sanitization_and_bounding() -> None:
    """Excessive attributes must be truncated to safe upper bounds."""
    now = datetime.now(UTC)
    oversized_attrs = {f"key_{i}": "x" * 1000 for i in range(50)}
    mp = MetricPoint(
        timestamp=now,
        service_name="order-service",
        metric_name="test.metric",
        value=1.0,
        attributes=oversized_attrs,
    )
    # Capped at 32 items
    assert len(mp.attributes) <= 32
    # Value capped at 512 chars
    for v in mp.attributes.values():
        assert len(str(v)) <= 512


def test_log_record_valid_and_sanitized() -> None:
    """Structured logs must sanitize null bytes and validate severity."""
    now = datetime.now(UTC)
    log = LogRecordSchema(
        timestamp=now,
        service_name="payment-service",
        severity="ERROR",
        message="Failure processing credit card\x00 with null bytes",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="abcdef0123456789",
        exception_type="PaymentException",
    )
    assert "\x00" not in log.message
    assert log.severity == "ERROR"
    assert log.span_id == "abcdef0123456789"


def test_log_record_invalid_span_id() -> None:
    """Span ID must be 16 hexadecimal characters."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        LogRecordSchema(
            timestamp=now,
            service_name="payment-service",
            severity="INFO",
            message="Test message",
            span_id="short_id",
        )


def test_span_record_valid_and_duration_computed() -> None:
    """Span start and end timestamps must automatically compute duration."""
    start = datetime.now(UTC)
    end = start + timedelta(milliseconds=125.5)
    span = SpanRecordSchema(
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="abcdef0123456789",
        parent_span_id=None,
        service_name="order-service",
        operation_name="POST /orders",
        start_time=start,
        end_time=end,
    )
    assert span.duration_ms is not None
    assert abs(span.duration_ms - 125.5) < 1.0
    assert span.status_code == "UNSET"


def test_span_record_end_before_start_rejected() -> None:
    """Span where end_time precedes start_time must raise ValidationError."""
    start = datetime.now(UTC)
    end = start - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        SpanRecordSchema(
            trace_id="0123456789abcdef0123456789abcdef",
            span_id="abcdef0123456789",
            service_name="order-service",
            operation_name="POST /orders",
            start_time=start,
            end_time=end,
        )


def test_batch_payload_constraints() -> None:
    """Batch envelopes must enforce min 1 and max 1000 records."""
    with pytest.raises(ValidationError):
        MetricBatchRequest(items=[])

    with pytest.raises(ValidationError):
        LogBatchRequest(items=[])

    with pytest.raises(ValidationError):
        TraceBatchRequest(items=[])


def test_experiment_create_request_bounds() -> None:
    """Experiment payload must validate bounded durations and supported fault types."""
    # Valid
    req = ExperimentCreateRequest(
        scenario_name="payment_slowdown",
        target_service="payment-service",
        fault_type="latency_spike",
        duration_seconds=30,
        initiating_service="payment-service",
    )
    assert req.duration_seconds == 30

    # Duration below 5s
    with pytest.raises(ValidationError):
        ExperimentCreateRequest(
            scenario_name="payment_slowdown",
            target_service="payment-service",
            fault_type="latency_spike",
            duration_seconds=2,
            initiating_service="payment-service",
        )

    # Duration above 300s
    with pytest.raises(ValidationError):
        ExperimentCreateRequest(
            scenario_name="payment_slowdown",
            target_service="payment-service",
            fault_type="latency_spike",
            duration_seconds=600,
            initiating_service="payment-service",
        )

    # Unsupported fault type
    with pytest.raises(ValidationError):
        ExperimentCreateRequest(
            scenario_name="kill_host",
            target_service="payment-service",
            fault_type="destructive_shutdown",  # type: ignore[arg-type]
            duration_seconds=30,
            initiating_service="payment-service",
        )
