"""Integration tests for time-bounded telemetry query APIs."""

from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ingestion.generator import DeterministicTelemetryGenerator


def test_query_metrics_filters_and_pagination(client: TestClient, test_db_session: Session) -> None:
    """Query metrics by service, name, and verify deterministic limit/offset pagination."""
    gen = DeterministicTelemetryGenerator(seed=42)
    batch = gen.generate_normal_traffic(count=10)
    DeterministicTelemetryGenerator.ingest_batch_to_database(test_db_session, batch)

    # 1. Query by service_name
    resp = client.get("/api/v1/telemetry/metrics?service_name=order-service&limit=5")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["limit"] == 5
    assert len(body["items"]) == 5
    assert all(item["service_name"] == "order-service" for item in body["items"])
    assert body["total"] > 5

    # 2. Query with offset pagination
    resp_page2 = client.get("/api/v1/telemetry/metrics?service_name=order-service&limit=5&offset=5")
    assert resp_page2.status_code == status.HTTP_200_OK
    body2 = resp_page2.json()
    assert len(body2["items"]) == 5
    # First item of page 2 should not equal first item of page 1
    assert body2["items"][0]["timestamp"] != body["items"][0]["timestamp"]


def test_query_metrics_invalid_time_window_rejected(client: TestClient) -> None:
    """End time preceding start time must trigger RFC 7807 validation error."""
    now = datetime.now(UTC)
    start = now
    end = now - timedelta(hours=1)

    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = client.get(f"/api/v1/telemetry/metrics?start_time={start_str}&end_time={end_str}")
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    problem = resp.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert "precede" in problem["detail"]


def test_query_logs_by_severity_and_trace_id(client: TestClient, test_db_session: Session) -> None:
    """Query structured logs filtered by severity and trace ID."""
    gen = DeterministicTelemetryGenerator(seed=77)
    # Generate batch with error surge to get ERROR logs
    batch = gen.generate_fault_traffic(
        fault_type="error_surge",
        experiment_id="exp_log_query",
        count=5,
        custom_params={"error_rate": 1.0},
    )
    DeterministicTelemetryGenerator.ingest_batch_to_database(test_db_session, batch)

    # Query ERROR severity
    resp = client.get("/api/v1/telemetry/logs?severity=ERROR")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] >= 1
    assert all(log["severity"] == "ERROR" for log in body["items"])

    # Query by specific trace_id
    sample_trace_id = batch.transactions[0].trace_id
    resp_trace = client.get(f"/api/v1/telemetry/logs?trace_id={sample_trace_id}")
    assert resp_trace.status_code == status.HTTP_200_OK
    trace_body = resp_trace.json()
    assert all(log["trace_id"] == sample_trace_id for log in trace_body["items"])


def test_query_traces_and_waterfall(client: TestClient, test_db_session: Session) -> None:
    """Retrieve trace spans and hierarchical waterfall tree."""
    gen = DeterministicTelemetryGenerator(seed=88)
    batch = gen.generate_normal_traffic(count=2)
    DeterministicTelemetryGenerator.ingest_batch_to_database(test_db_session, batch)

    # Filter traces by status_code
    resp = client.get("/api/v1/telemetry/traces?status_code=OK")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] >= 2

    # Fetch complete waterfall for trace_id
    target_trace_id = batch.transactions[0].trace_id
    resp_wf = client.get(f"/api/v1/telemetry/traces/{target_trace_id}")
    assert resp_wf.status_code == status.HTTP_200_OK
    wf_body = resp_wf.json()
    assert wf_body["trace_id"] == target_trace_id
    assert wf_body["span_count"] >= 3  # Root order, child payment, grandchild db

    # Root span (parent_span_id is None) must come first
    root_span = wf_body["spans"][0]
    assert root_span["parent_span_id"] is None
    assert root_span["service_name"] == "order-service"


def test_query_waterfall_not_found(client: TestClient) -> None:
    """Non-existent trace ID returns 404."""
    resp = client.get("/api/v1/telemetry/traces/00000000000000000000000000000000")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
