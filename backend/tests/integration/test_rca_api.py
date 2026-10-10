"""Integration tests for Phase 4 root-cause analysis endpoints."""

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.ml import AnomalyDetectionModel
from app.db.models.telemetry import TraceSpanModel
from app.db.models.topology import TopologyEdgeModel, TopologySnapshotModel


def _hex_trace(id_str: str) -> str:
    return hashlib.md5(id_str.encode()).hexdigest()


def _hex_span(id_str: str) -> str:
    return hashlib.md5(id_str.encode()).hexdigest()[:16]


def test_analyze_root_cause_endpoint_and_retrieval(
    client: TestClient, test_db_session: Session
) -> None:
    """Verify root-cause analysis execution, ranking output, and persistence retrieval."""
    now = datetime.now(UTC) - timedelta(minutes=5)

    # 1. Seed topology snapshot: checkout -> payment
    snap = TopologySnapshotModel(
        id="topo-rca-test-01",
        version="v1.0.0",
        data_source="trace_spans",
        time_window_start=now - timedelta(hours=1),
        time_window_end=now,
        service_count=2,
        edge_count=1,
        services_json=["checkout-service", "payment-service"],
        created_at=now,
    )
    edge = TopologyEdgeModel(
        topology_id="topo-rca-test-01",
        caller_service="checkout-service",
        downstream_service="payment-service",
        call_count=100,
        error_count=20,
        error_rate=0.20,
        first_seen=now - timedelta(hours=1),
        last_seen=now,
    )
    test_db_session.add_all([snap, edge])

    # 2. Seed anomalies: payment-service at t=now, checkout-service at t=now+5s
    anom_payment = AnomalyDetectionModel(
        timestamp=now,
        service_name="payment-service",
        detector_name="threshold",
        model_version="v1.0",
        is_anomaly=True,
        anomaly_score=0.92,
        severity="CRITICAL",
        triggering_features=["error_rate", "latency_p95_ms"],
        explanation="Payment database timeout",
        trace_id=_hex_trace("tr-rca-01"),
        created_at=now,
    )
    anom_checkout = AnomalyDetectionModel(
        timestamp=now + timedelta(seconds=5),
        service_name="checkout-service",
        detector_name="threshold",
        model_version="v1.0",
        is_anomaly=True,
        anomaly_score=0.70,
        severity="HIGH",
        triggering_features=["error_rate"],
        explanation="Downstream payment error",
        trace_id=_hex_trace("tr-rca-01"),
        created_at=now,
    )
    test_db_session.add_all([anom_payment, anom_checkout])

    # 3. Seed error spans: payment is leaf error
    tr_id = _hex_trace("tr-rca-01")
    sp_parent = _hex_span("sp-parent")
    sp_child = _hex_span("sp-child")

    span1 = TraceSpanModel(
        trace_id=tr_id,
        span_id=sp_parent,
        parent_span_id=None,
        service_name="checkout-service",
        operation_name="checkout_op",
        start_time=now,
        end_time=now + timedelta(milliseconds=200),
        duration_ms=200.0,
        status_code="ERROR",
    )
    span2 = TraceSpanModel(
        trace_id=tr_id,
        span_id=sp_child,
        parent_span_id=sp_parent,
        service_name="payment-service",
        operation_name="payment_op",
        start_time=now + timedelta(milliseconds=20),
        end_time=now + timedelta(milliseconds=180),
        duration_ms=160.0,
        status_code="ERROR",
    )
    test_db_session.add_all([span1, span2])
    test_db_session.commit()

    # Execute RCA API
    payload = {
        "lookback_minutes": 30,
        "topology_id": "topo-rca-test-01",
        "persist": True,
    }
    resp = client.post("/api/v1/incidents/root-cause/analyze", json=payload)
    assert resp.status_code == status.HTTP_200_OK

    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["top_candidate_service"] == "payment-service"
    assert body["confidence_score"] > 0.60
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["service_name"] == "payment-service"
    assert body["candidates"][0]["rank"] == 1
    assert body["candidates"][1]["service_name"] == "checkout-service"
    assert body["candidates"][1]["rank"] == 2

    analysis_id = body["analysis_id"]

    # Retrieve persisted analysis by ID
    get_resp = client.get(f"/api/v1/incidents/root-cause/{analysis_id}")
    assert get_resp.status_code == status.HTTP_200_OK
    get_body = get_resp.json()
    assert get_body["analysis_id"] == analysis_id
    assert get_body["top_candidate_service"] == "payment-service"


def test_list_root_cause_analyses_pagination(client: TestClient, test_db_session: Session) -> None:
    """Verify paginated listing of past RCA runs."""
    resp = client.get("/api/v1/incidents/root-cause?limit=10&offset=0")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert "total" in body
    assert "items" in body
    assert body["limit"] == 10
    assert body["offset"] == 0


def test_analyze_root_cause_empty_telemetry_returns_insufficient_evidence(
    client: TestClient,
) -> None:
    """Verify empty observation window returns INSUFFICIENT_EVIDENCE status without failing."""
    payload = {
        "time_window_start": "2026-01-01T00:00:00Z",
        "time_window_end": "2026-01-01T00:10:00Z",
        "persist": False,
    }
    resp = client.post("/api/v1/incidents/root-cause/analyze", json=payload)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "INSUFFICIENT_EVIDENCE"
    assert body["top_candidate_service"] is None
    assert body["confidence_score"] is None
    assert "No operational anomalies detected" in body["summary_explanation"]


def test_get_root_cause_analysis_nonexistent_returns_404(client: TestClient) -> None:
    """Verify querying nonexistent RCA run returns 404."""
    resp = client.get("/api/v1/incidents/root-cause/rca-nonexistent-123")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
