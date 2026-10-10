"""Unit tests for service dependency topology extraction, graph algorithms, and edge conventions."""

import hashlib
from datetime import UTC, datetime, timedelta

from app.schemas.telemetry import SpanRecordSchema
from app.topology.extractor import build_topology_from_spans
from app.topology.models import TopologyGraph


def _make_span(
    trace_id: str,
    span_id: str,
    service_name: str,
    parent_span_id: str | None = None,
    operation_name: str = "test_op",
    duration_ms: float = 25.0,
    status_code: str = "OK",
    start_time: datetime | None = None,
) -> SpanRecordSchema:
    now = start_time or datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    norm_tr = hashlib.md5(trace_id.encode("utf-8")).hexdigest()
    norm_sp = hashlib.md5(span_id.encode("utf-8")).hexdigest()[:16]
    norm_parent = (
        hashlib.md5(parent_span_id.encode("utf-8")).hexdigest()[:16] if parent_span_id else None
    )

    return SpanRecordSchema(
        trace_id=norm_tr,
        span_id=norm_sp,
        parent_span_id=norm_parent,
        service_name=service_name,
        operation_name=operation_name,
        start_time=now,
        end_time=now + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        status_code=status_code,
    )


def test_caller_to_downstream_direction() -> None:
    """Verify edge direction is strictly caller -> downstream (parent -> child)."""
    # Parent span in checkout-service calls child span in payment-service
    parent_span = _make_span(
        trace_id="tr-001",
        span_id="sp-parent",
        service_name="checkout-service",
        parent_span_id=None,
    )
    child_span = _make_span(
        trace_id="tr-001",
        span_id="sp-child",
        service_name="payment-service",
        parent_span_id="sp-parent",
    )

    t0 = datetime(2026, 10, 10, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)

    graph = build_topology_from_spans(
        spans=[parent_span, child_span],
        time_window_start=t0,
        time_window_end=t1,
    )

    assert "checkout-service" in graph.nodes
    assert "payment-service" in graph.nodes
    assert ("checkout-service", "payment-service") in graph.edges

    edge = graph.edges[("checkout-service", "payment-service")]
    assert edge.caller_service == "checkout-service"
    assert edge.downstream_service == "payment-service"
    assert edge.call_count == 1
    assert edge.error_count == 0


def test_parent_child_span_reconstruction_multi_hop() -> None:
    """Verify multi-hop cross-service call tree reconstruction."""
    spans = [
        _make_span("tr-1", "sp-1", "api-gateway", None),
        _make_span("tr-1", "sp-2", "order-service", "sp-1"),
        _make_span("tr-1", "sp-3", "payment-service", "sp-2"),
        _make_span("tr-1", "sp-4", "fraud-service", "sp-3"),
    ]

    t0 = datetime(2026, 10, 10, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)

    graph = build_topology_from_spans(spans=spans, time_window_start=t0, time_window_end=t1)

    assert ("api-gateway", "order-service") in graph.edges
    assert ("order-service", "payment-service") in graph.edges
    assert ("payment-service", "fraud-service") in graph.edges

    # Check path finding
    path = graph.find_path("api-gateway", "fraud-service")
    assert path == ["api-gateway", "order-service", "payment-service", "fraud-service"]

    # Check downstream traversal
    downstream = graph.get_all_downstream("api-gateway")
    assert downstream == {"order-service", "payment-service", "fraud-service"}

    # Check upstream traversal
    upstream = graph.get_all_upstream("fraud-service")
    assert upstream == {"payment-service", "order-service", "api-gateway"}


def test_missing_parent_spans_handled_safely() -> None:
    """Verify missing parent spans do not raise exceptions and don't invent edges."""
    orphan_span = _make_span(
        trace_id="tr-002",
        span_id="sp-orphan",
        service_name="payment-service",
        parent_span_id="sp-nonexistent-parent",
    )

    t0 = datetime(2026, 10, 10, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)

    graph = build_topology_from_spans(spans=[orphan_span], time_window_start=t0, time_window_end=t1)

    assert "payment-service" in graph.nodes
    assert len(graph.edges) == 0
    assert graph.diagnostics["unresolved_parents"] == 1


def test_malformed_spans_rejected_safely() -> None:
    """Verify spans with self-loops or missing required fields are counted as malformed and skipped."""
    valid_span = _make_span("tr-1", "sp-1", "order-service")
    # Self-loop span where parent_id == span_id or missing service_name
    bad_span_self_loop = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": "b" * 16,  # Self-loop!
        "service_name": "order-service",
        "start_time": datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
        "duration_ms": 10.0,
        "status_code": "OK",
    }
    bad_span_missing_svc = {
        "trace_id": "a" * 32,
        "span_id": "c" * 16,
        "parent_span_id": None,
        "service_name": "",  # Empty service name!
        "start_time": datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
    }

    t0 = datetime(2026, 10, 10, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)

    graph = build_topology_from_spans(
        spans=[valid_span, bad_span_self_loop, bad_span_missing_svc],
        time_window_start=t0,
        time_window_end=t1,
    )
    assert "order-service" in graph.nodes
    assert graph.diagnostics["malformed_spans"] == 2


def test_repeated_observations_aggregated() -> None:
    """Verify repeated cross-service spans aggregate counts, errors, and latencies."""
    spans = [
        _make_span("tr-1", "sp-1", "order-service"),
        _make_span("tr-1", "sp-2", "payment-service", "sp-1", duration_ms=50.0, status_code="OK"),
        _make_span("tr-2", "sp-3", "order-service"),
        _make_span(
            "tr-2", "sp-4", "payment-service", "sp-3", duration_ms=150.0, status_code="ERROR"
        ),
    ]

    t0 = datetime(2026, 10, 10, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)

    graph = build_topology_from_spans(spans=spans, time_window_start=t0, time_window_end=t1)

    edge = graph.edges[("order-service", "payment-service")]
    assert edge.call_count == 2
    assert edge.error_count == 1
    assert edge.error_rate == 0.5
    assert edge.latency_p50_ms == 100.0
    assert edge.latency_p95_ms == 145.0


def test_deterministic_topology_snapshots() -> None:
    """Verify graph reconstruction is strictly deterministic given the same inputs."""
    spans = [
        _make_span("tr-1", "sp-1", "service-a"),
        _make_span("tr-1", "sp-2", "service-b", "sp-1", duration_ms=30.0),
        _make_span("tr-2", "sp-3", "service-b"),
        _make_span("tr-2", "sp-4", "service-c", "sp-3", duration_ms=45.0),
    ]

    t0 = datetime(2026, 10, 10, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)

    g1 = build_topology_from_spans(spans, t0, t1, version="v1")
    g2 = build_topology_from_spans(spans, t0, t1, version="v1")

    assert g1.to_dict() == g2.to_dict()


def test_cyclic_graphs_cycle_detection_and_bounded_traversal() -> None:
    """Verify cyclic dependencies are detected and do not cause infinite recursion."""
    graph = TopologyGraph()
    graph.add_edge("service-a", "service-b")
    graph.add_edge("service-b", "service-c")
    graph.add_edge("service-c", "service-a")  # Cycle!

    assert graph.has_cycle() is True

    # Bounded traversal must terminate without recursion error
    downstream_a = graph.get_all_downstream("service-a", max_depth=5)
    assert downstream_a == {"service-b", "service-c"}

    upstream_a = graph.get_all_upstream("service-a", max_depth=5)
    assert upstream_a == {"service-c", "service-b"}


def test_time_bounded_topology_extraction() -> None:
    """Verify spans outside the window are excluded."""
    t0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)

    in_window = _make_span("tr-1", "sp-1", "svc-1", start_time=t0 + timedelta(minutes=10))
    outside_window = _make_span("tr-2", "sp-2", "svc-2", start_time=t0 - timedelta(hours=2))

    graph = build_topology_from_spans([in_window, outside_window], t0, t1)
    assert "svc-1" in graph.nodes
    assert "svc-2" not in graph.nodes
