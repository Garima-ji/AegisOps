"""Integration tests for telemetry batch ingestion endpoints."""

from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.telemetry import LogRecordModel, MetricRecordModel, ServiceModel, TraceSpanModel


def test_ingest_metrics_batch_success(client: TestClient, test_db_session: Session) -> None:
    """Ingesting valid metrics must persist records and auto-register service."""
    now = datetime.now(UTC)
    payload = {
        "items": [
            {
                "timestamp": now.isoformat(),
                "service_name": "order-service",
                "environment": "production",
                "metric_name": "http.server.requests.count",
                "value": 42.0,
                "unit": "count",
                "attributes": {"handler": "orders"},
            },
            {
                "timestamp": (now - timedelta(seconds=1)).isoformat(),
                "service_name": "order-service",
                "environment": "production",
                "metric_name": "http.server.request.duration_ms",
                "value": 115.4,
                "unit": "ms",
                "attributes": {"handler": "orders"},
            },
        ]
    }

    resp = client.post("/api/v1/telemetry/metrics", json=payload)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "success"
    assert body["received"] == 2
    assert body["accepted"] == 2
    assert body["rejected"] == 0

    # Verify database persistence
    metrics = list(test_db_session.execute(select(MetricRecordModel)).scalars().all())
    assert len(metrics) == 2

    # Verify service registry auto-upsert
    svc = test_db_session.execute(
        select(ServiceModel).where(ServiceModel.name == "order-service")
    ).scalar_one_or_none()
    assert svc is not None
    assert svc.status == "healthy"


def test_ingest_metrics_partial_failure_on_future_timestamp(
    client: TestClient, test_db_session: Session
) -> None:
    """Records exceeding the future timestamp limit must be rejected while valid records are persisted."""
    now = datetime.now(UTC)
    future_time = now + timedelta(days=2)  # Far exceeds 5m tolerance
    valid_time = now - timedelta(seconds=10)

    payload = {
        "items": [
            {
                "timestamp": valid_time.isoformat(),
                "service_name": "payment-service",
                "metric_name": "valid.metric",
                "value": 10.0,
            },
            {
                "timestamp": future_time.isoformat(),
                "service_name": "payment-service",
                "metric_name": "future.metric",
                "value": 20.0,
            },
        ]
    }

    resp = client.post("/api/v1/telemetry/metrics", json=payload)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "partial_success"
    assert body["received"] == 2
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert len(body["errors"]) == 1
    assert body["errors"][0]["field"] == "timestamp"

    # Only 1 record should exist in the database
    metrics = list(test_db_session.execute(select(MetricRecordModel)).scalars().all())
    assert len(metrics) == 1
    assert metrics[0].metric_name == "valid.metric"


def test_ingest_logs_batch_success(client: TestClient, test_db_session: Session) -> None:
    """Ingesting structured logs must persist logs and correlate trace/span identifiers."""
    now = datetime.now(UTC)
    trace_id = "11112222333344445555666677778888"
    span_id = "aaaabbbbccccdddd"

    payload = {
        "items": [
            {
                "timestamp": now.isoformat(),
                "service_name": "payment-service",
                "environment": "production",
                "severity": "ERROR",
                "message": "Payment authorization gateway timeout",
                "trace_id": trace_id,
                "span_id": span_id,
                "exception_type": "GatewayTimeout",
                "error_class": "NetworkError",
                "attributes": {"retry_attempt": 3},
            }
        ]
    }

    resp = client.post("/api/v1/telemetry/logs", json=payload)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "success"
    assert body["accepted"] == 1

    log_entry = test_db_session.execute(select(LogRecordModel)).scalar_one()
    assert log_entry.service_name == "payment-service"
    assert log_entry.severity == "ERROR"
    assert log_entry.trace_id == trace_id
    assert log_entry.span_id == span_id


def test_ingest_traces_batch_success_and_duplicate_rejection(
    client: TestClient, test_db_session: Session
) -> None:
    """Trace spans must persist and reject duplicate span occurrences within the same batch."""
    now = datetime.now(UTC)
    trace_id = "abcdefabcdefabcdefabcdefabcdefab"
    root_span_id = "0102030405060708"
    child_span_id = "090a0b0c0d0e0f10"

    payload = {
        "items": [
            {
                "trace_id": trace_id,
                "span_id": root_span_id,
                "parent_span_id": None,
                "service_name": "order-service",
                "operation_name": "POST /orders",
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(milliseconds=80)).isoformat(),
                "status_code": "OK",
            },
            {
                "trace_id": trace_id,
                "span_id": child_span_id,
                "parent_span_id": root_span_id,
                "service_name": "payment-service",
                "operation_name": "POST /payments",
                "start_time": (now + timedelta(milliseconds=10)).isoformat(),
                "end_time": (now + timedelta(milliseconds=70)).isoformat(),
                "status_code": "OK",
            },
            # Duplicate of child span in the same batch
            {
                "trace_id": trace_id,
                "span_id": child_span_id,
                "parent_span_id": root_span_id,
                "service_name": "payment-service",
                "operation_name": "POST /payments",
                "start_time": (now + timedelta(milliseconds=10)).isoformat(),
                "end_time": (now + timedelta(milliseconds=70)).isoformat(),
                "status_code": "OK",
            },
        ]
    }

    resp = client.post("/api/v1/telemetry/traces", json=payload)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "partial_success"
    assert body["received"] == 3
    assert body["accepted"] == 2
    assert body["rejected"] == 1

    spans = list(test_db_session.execute(select(TraceSpanModel)).scalars().all())
    assert len(spans) == 2


def test_pipeline_metrics_endpoint(client: TestClient) -> None:
    """Operational telemetry endpoint must return low-cardinality counters and durations."""
    # First ingest a metric
    client.post(
        "/api/v1/telemetry/metrics",
        json={
            "items": [
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "service_name": "order-service",
                    "metric_name": "test.metric",
                    "value": 1.0,
                }
            ]
        },
    )

    resp = client.get("/api/v1/telemetry/pipeline-metrics")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert "counters" in body
    assert body["counters"]["telemetry.metrics.received_total"] >= 1
    assert body["counters"]["telemetry.metrics.accepted_total"] >= 1
    assert "durations_ms" in body
