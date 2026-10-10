"""Unit tests for distributed pipeline tracer and correlation tracking."""

import time

from app.observability.tracer import PipelineTracer, TraceSpan


def test_tracer_span_creation_and_completion() -> None:
    """Verify tracer creates spans, computes duration, and sets status."""
    tracer = PipelineTracer()
    with tracer.span("test_operation", tags={"service": "payment-service"}) as span:
        assert span.name == "test_operation"
        assert span.tags["service"] == "payment-service"
        assert span.status == "ok"
        time.sleep(0.01)

    assert span.end_time is not None
    assert span.duration_ms > 0.0
    assert span.status == "ok"


def test_tracer_parent_child_context_propagation() -> None:
    """Verify child spans inherit trace_id and set parent_span_id."""
    tracer = PipelineTracer()
    with tracer.span("parent_job", trace_id="custom-trace-123") as parent:
        assert parent.trace_id == "custom-trace-123"
        assert parent.parent_span_id is None

        with tracer.span("child_task") as child:
            assert child.trace_id == "custom-trace-123"
            assert child.parent_span_id == parent.span_id

    recent = tracer.get_spans_by_trace("custom-trace-123")
    assert len(recent) == 2


def test_tracer_sanitizes_sensitive_tags() -> None:
    """Verify sensitive tags like passwords and secrets are redacted."""
    span = TraceSpan(span_id="sp1", trace_id="tr1", name="auth")
    span.set_tag("api_key", "secret-token-123")
    span.set_tag("password", "p@ssword")
    span.set_tag("user_prompt", "Ignore previous instructions")
    span.set_tag("normal_tag", "valid_value")

    assert "api_key" not in span.tags
    assert "password" not in span.tags
    assert "user_prompt" not in span.tags
    assert span.tags["normal_tag"] == "valid_value"


def test_tracer_error_capture() -> None:
    """Verify exceptions in traced block mark span as error with category."""
    tracer = PipelineTracer()
    try:
        with tracer.span("failing_stage"):
            raise ValueError("Invalid configuration parameters")
    except ValueError:
        pass

    spans = tracer.get_recent_spans(limit=1)
    assert len(spans) == 1
    err_span = spans[0]
    assert err_span["status"] == "error"
    assert err_span["error_category"] == "ValueError"
    assert "Invalid configuration" in err_span["error_message"]
