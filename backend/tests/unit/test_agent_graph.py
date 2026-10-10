"""Unit tests for LangGraph state machine execution, transitions, and candidate preservation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent.graph import build_investigation_graph
from app.agent.providers import FakeDeterministicProvider
from app.db.base import Base
from app.db.models.ml import AnomalyDetectionModel
from app.db.models.topology import IncidentAnalysisModel, TopologyEdgeModel, TopologySnapshotModel


@pytest.fixture
def test_db() -> Session:
    """In-memory SQLite database populated with topology and anomaly events."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def test_investigation_graph_end_to_end_success(test_db: Session) -> None:
    """Test full LangGraph execution from initialize to report generation."""
    now = datetime.now(UTC)
    st = now - timedelta(minutes=20)
    et = now

    # Setup topology snapshot
    snap = TopologySnapshotModel(
        id="topo-graph-1",
        version="v1.0.0",
        data_source="test",
        time_window_start=st,
        time_window_end=et,
        service_count=2,
        edge_count=1,
        services_json=["order-service", "payment-service"],
    )
    edge = TopologyEdgeModel(
        topology_id="topo-graph-1",
        caller_service="order-service",
        downstream_service="payment-service",
        call_count=50,
        error_count=5,
        error_rate=0.1,
        first_seen=st,
        last_seen=et,
    )
    # Setup anomaly
    anom = AnomalyDetectionModel(
        timestamp=now - timedelta(minutes=5),
        service_name="payment-service",
        detector_name="rolling_mad",
        model_version="v1",
        is_anomaly=True,
        anomaly_score=0.95,
        severity="HIGH",
        explanation="Latency surge",
    )
    # Setup Phase 4 analysis record
    rca = IncidentAnalysisModel(
        id="ana-graph-1",
        incident_id="inc-graph-1",
        status="COMPLETED",
        time_window_start=st,
        time_window_end=et,
        affected_services=["order-service", "payment-service"],
        top_candidate_service="payment-service",
        confidence_score=0.92,
        candidates_json=[
            {
                "service_name": "payment-service",
                "rank": 1,
                "composite_score": 0.92,
                "explanation": "Initiating service for downstream timeouts",
            },
            {
                "service_name": "order-service",
                "rank": 2,
                "composite_score": 0.45,
                "explanation": "Symptom service showing cascade delays",
            },
        ],
    )
    test_db.add(snap)
    test_db.add(edge)
    test_db.add(anom)
    test_db.add(rca)
    test_db.commit()

    graph = build_investigation_graph()
    initial_state = {
        "investigation_id": "inv-graph-test-1",
        "incident_id": "inc-graph-1",
        "time_window_start": st,
        "time_window_end": et,
        "db_session": test_db,
        "llm_provider": FakeDeterministicProvider(),
        "require_approval_by_default": False,
    }

    final_state = graph.invoke(initial_state)

    assert final_state["investigation_id"] == "inv-graph-test-1"
    assert final_state["current_step"] == "finalize_and_audit"
    assert final_state["is_abstaining"] is False

    report = final_state["final_report"]
    assert report is not None
    assert report["leading_candidate"] == "payment-service"
    assert len(report["supporting_evidence_citations"]) > 0

    # Strict Candidate Preservation Check: Phase 4 ranking must remain unchanged
    p4_ranking = report["phase4_root_cause_ranking"]
    assert len(p4_ranking) == 2
    assert p4_ranking[0]["service_name"] == "payment-service"
    assert p4_ranking[0]["rank"] == 1
    assert p4_ranking[1]["service_name"] == "order-service"
    assert p4_ranking[1]["rank"] == 2


def test_investigation_graph_abstains_on_missing_telemetry(test_db: Session) -> None:
    """Test investigation cleanly abstains when no anomalies or root causes exist."""
    now = datetime.now(UTC)
    st = now - timedelta(minutes=20)
    et = now

    graph = build_investigation_graph()
    initial_state = {
        "investigation_id": "inv-abstain-test",
        "incident_id": "inc-empty",
        "time_window_start": st,
        "time_window_end": et,
        "db_session": test_db,
        "llm_provider": FakeDeterministicProvider(),
        "require_approval_by_default": True,
    }

    final_state = graph.invoke(initial_state)

    assert final_state["is_abstaining"] is True
    assert "INSUFFICIENT_EVIDENCE" in (final_state.get("abstention_reason") or "")

    report = final_state["final_report"]
    assert report["is_abstained"] is True
    assert report["leading_candidate"] is None
    assert report["confidence_classification"] == "INSUFFICIENT_EVIDENCE"
