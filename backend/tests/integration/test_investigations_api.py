"""Integration tests for Phase 5 agentic investigation and remediation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.ml import AnomalyDetectionModel
from app.db.models.topology import IncidentAnalysisModel, TopologyEdgeModel, TopologySnapshotModel


def test_investigation_api_full_lifecycle(client: TestClient, test_db_session: Session) -> None:
    """Test start investigation, summary, report, audit events, dry-run, approve, and simulation."""
    now = datetime.now(UTC)
    st = now - timedelta(minutes=25)
    et = now

    # 1. Seed topology snapshot
    snap = TopologySnapshotModel(
        id="topo-inv-api-1",
        version="v1.0.0",
        data_source="test",
        time_window_start=st,
        time_window_end=et,
        service_count=2,
        edge_count=1,
        services_json=["order-service", "payment-service"],
        created_at=now,
    )
    edge = TopologyEdgeModel(
        topology_id="topo-inv-api-1",
        caller_service="order-service",
        downstream_service="payment-service",
        call_count=120,
        error_count=15,
        error_rate=0.125,
        latency_p95_ms=900.0,
        first_seen=st,
        last_seen=et,
    )
    # 2. Seed anomaly
    anom = AnomalyDetectionModel(
        timestamp=now - timedelta(minutes=10),
        service_name="payment-service",
        detector_name="rolling_mad",
        model_version="v1",
        is_anomaly=True,
        anomaly_score=0.94,
        severity="HIGH",
        explanation="Payment latency surge",
    )
    # 3. Seed RCA
    rca = IncidentAnalysisModel(
        id="rca-inv-api-1",
        incident_id="inc-api-test-001",
        status="COMPLETED",
        time_window_start=st,
        time_window_end=et,
        affected_services=["order-service", "payment-service"],
        top_candidate_service="payment-service",
        confidence_score=0.94,
        candidates_json=[
            {
                "service_name": "payment-service",
                "rank": 1,
                "composite_score": 0.94,
                "explanation": "Initiating delay",
            }
        ],
        created_at=now,
    )
    test_db_session.add(snap)
    test_db_session.add(edge)
    test_db_session.add(anom)
    test_db_session.add(rca)
    test_db_session.commit()

    # 4. POST /api/v1/investigations
    start_payload = {
        "incident_id": "inc-api-test-001",
        "time_window_start": st.isoformat(),
        "time_window_end": et.isoformat(),
        "require_approval": True,
    }
    resp = client.post("/api/v1/investigations", json=start_payload)
    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    data = resp.json()
    inv_id = data["id"]
    assert inv_id.startswith("inv-")
    assert data["leading_candidate"] == "payment-service"
    assert data["status"] == "COMPLETED"

    # 5. GET /api/v1/investigations/{inv_id}
    resp_get = client.get(f"/api/v1/investigations/{inv_id}")
    assert resp_get.status_code == status.HTTP_200_OK
    assert resp_get.json()["id"] == inv_id

    # 6. GET /api/v1/investigations/{inv_id}/report
    resp_rep = client.get(f"/api/v1/investigations/{inv_id}/report")
    assert resp_rep.status_code == status.HTTP_200_OK
    rep_data = resp_rep.json()
    assert rep_data["investigation_id"] == inv_id
    assert rep_data["leading_candidate"] == "payment-service"
    assert len(rep_data["phase4_root_cause_ranking"]) > 0
    assert len(rep_data["supporting_evidence_citations"]) > 0

    # 7. GET /api/v1/investigations/{inv_id}/events
    resp_evts = client.get(f"/api/v1/investigations/{inv_id}/events")
    assert resp_evts.status_code == status.HTTP_200_OK
    evts_data = resp_evts.json()
    assert len(evts_data) >= 3

    # 8. POST /api/v1/investigations/{inv_id}/remediation/dry-run
    resp_dry = client.post(f"/api/v1/investigations/{inv_id}/remediation/dry-run")
    assert resp_dry.status_code == status.HTTP_200_OK
    assert resp_dry.json()["status"] == "DRY_RUN"

    # 9. POST /api/v1/investigations/{inv_id}/remediation/approve
    resp_app = client.post(
        f"/api/v1/investigations/{inv_id}/remediation/approve",
        json={"operator_id": "test-admin"},
    )
    assert resp_app.status_code == status.HTTP_200_OK
    assert resp_app.json()["approval_status"] == "APPROVED"

    # 10. POST /api/v1/investigations/{inv_id}/remediation/execute-simulation
    resp_sim = client.post(f"/api/v1/investigations/{inv_id}/remediation/execute-simulation")
    assert resp_sim.status_code == status.HTTP_200_OK
    sim_data = resp_sim.json()
    assert sim_data["simulation_status"] == "SIMULATED_SUCCESS"


def test_investigation_api_not_found_and_validation(client: TestClient) -> None:
    """Test 404 on nonexistent investigation and 400 on invalid time range."""
    # 404
    resp = client.get("/api/v1/investigations/inv-nonexistent")
    assert resp.status_code == status.HTTP_404_NOT_FOUND

    # 400 Reversed time window
    now = datetime.now(UTC)
    invalid_payload = {
        "time_window_start": now.isoformat(),
        "time_window_end": (now - timedelta(minutes=10)).isoformat(),
    }
    resp_bad = client.post("/api/v1/investigations", json=invalid_payload)
    assert resp_bad.status_code == status.HTTP_400_BAD_REQUEST
