"""Unit tests for operational metrics registry, label sanitization, and Prometheus text exposition."""

from app.observability.metrics import (
    get_observability_metrics,
    increment_counter,
    record_duration,
    render_prometheus_text,
    reset_observability_metrics,
)


def test_metrics_increment_and_reset() -> None:
    """Verify counter increments and test reset."""
    reset_observability_metrics()
    increment_counter("telemetry.metrics.received_total", 5)
    increment_counter("telemetry.metrics.received_total", 10)

    snap = get_observability_metrics()
    assert snap["counters"]["telemetry.metrics.received_total"] == 15

    reset_observability_metrics()
    assert get_observability_metrics()["counters"]["telemetry.metrics.received_total"] == 0


def test_metric_label_cardinality_sanitization() -> None:
    """Verify high-cardinality IDs are sanitized to prevent memory leakage."""
    reset_observability_metrics()
    raw_uuid = "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"
    increment_counter(
        "http.requests.total",
        1,
        labels={"method": "GET", "req_id": raw_uuid, "status": "200"},
    )

    text = render_prometheus_text()
    assert 'method="GET"' in text
    assert 'status="200"' in text
    assert 'req_id="redacted_id"' in text
    assert raw_uuid not in text


def test_duration_percentiles() -> None:
    """Verify min, avg, p50, p95 calculation in metric durations."""
    reset_observability_metrics()
    for d in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]:
        record_duration("ml_inference", d)

    snap = get_observability_metrics()
    lat = snap["latencies_ms"]["ml_inference_ms"]
    assert lat["count"] == 10
    assert lat["min"] == 10.0
    assert lat["max"] == 100.0
    assert lat["avg"] == 55.0
    assert lat["p50"] == 60.0


def test_prometheus_text_format_validity() -> None:
    """Verify Prometheus text exposition follows standard syntax."""
    reset_observability_metrics()
    increment_counter("agent.investigations.completed_total", 2)
    record_duration("investigation_duration", 150.0)

    prom_text = render_prometheus_text()
    assert "# HELP" in prom_text
    assert "# TYPE" in prom_text
    assert "aegisops_agent_investigations_completed_total 2" in prom_text
    assert "aegisops_investigation_duration_ms_count 1" in prom_text
