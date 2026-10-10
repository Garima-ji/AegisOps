"""Integration tests for Phase 6 observability, manifest, and health endpoints."""

from fastapi.testclient import TestClient


def test_prometheus_metrics_endpoint(client: TestClient) -> None:
    """Verify /metrics returns HTTP 200 in Prometheus text exposition format."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "# HELP" in body
    assert "# TYPE" in body
    assert "aegisops_http_requests_total" in body


def test_system_manifest_endpoint(client: TestClient) -> None:
    """Verify /system/manifest returns HTTP 200 with platform version manifest."""
    response = client.get("/system/manifest")
    assert response.status_code == 200
    data = response.json()
    assert "platform_version" in data
    assert "feature_schema_version" in data
    assert "rca_algorithm_version" in data
    assert "agent_workflow_version" in data
    assert "detector_thresholds" in data


def test_observability_metrics_json_endpoint(client: TestClient) -> None:
    """Verify /api/v1/observability/metrics returns comprehensive JSON snapshot."""
    response = client.get("/api/v1/observability/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "counters" in data
    assert "latencies_ms" in data
    assert "timestamp" in data


def test_observability_spans_endpoint(client: TestClient) -> None:
    """Verify /api/v1/observability/spans returns list of recent traced spans."""
    # First make a request to generate a span
    client.get("/health/live")

    response = client.get("/api/v1/observability/spans?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "spans" in data
    assert isinstance(data["spans"], list)
    assert len(data["spans"]) > 0
    span = data["spans"][-1]
    assert "span_id" in span
    assert "trace_id" in span
    assert "name" in span
    assert span["status"] == "ok"


def test_readiness_probe_includes_phase6_dependencies(client: TestClient) -> None:
    """Verify /health/ready includes models and optional LLM dependency status."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"

    details = data["details"]
    assert "models" in details
    assert "optional_dependencies" in details
    assert "llm_provider" in details["optional_dependencies"]
