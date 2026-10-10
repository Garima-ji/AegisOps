"""Unit tests for bounded, typed incident investigation tools."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent.tools.remediation_tools import (
    EvaluateRemediationPolicyTool,
    ExecuteSimulatedRemediationTool,
)
from app.agent.tools.runbook_tools import RetrieveRunbookTool
from app.agent.tools.telemetry_tools import (
    GetAnomalyEventsTool,
    QueryServiceLogsTool,
    QueryServiceMetricsTool,
    QueryTraceSpansTool,
)
from app.agent.tools.topology_tools import GetServiceTopologyTool
from app.db.base import Base
from app.db.models.ml import AnomalyDetectionModel
from app.db.models.telemetry import LogRecordModel, MetricRecordModel, TraceSpanModel
from app.db.models.topology import TopologyEdgeModel, TopologySnapshotModel
from app.ingestion.fault_injection import fault_engine


@pytest.fixture
def in_memory_db() -> Session:
    """Isolated SQLite in-memory test database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def test_get_anomaly_events_valid_and_empty(in_memory_db: Session) -> None:
    """Test anomaly query returns bounded results and creates evidence records."""
    tool = GetAnomalyEventsTool()
    now = datetime.now(UTC)

    # Empty query
    resp = tool.execute_with_audit(
        db=in_memory_db,
        start_time=now - timedelta(minutes=15),
        end_time=now,
        limit=10,
    )
    assert resp.success is True
    assert resp.audit.record_count == 0
    assert resp.data["count"] == 0

    # Insert an anomaly record
    anom = AnomalyDetectionModel(
        timestamp=now - timedelta(minutes=5),
        service_name="payment-service",
        detector_name="rolling_mad",
        model_version="v1",
        is_anomaly=True,
        anomaly_score=0.92,
        severity="HIGH",
        explanation="Latency surge",
    )
    in_memory_db.add(anom)
    in_memory_db.commit()

    resp2 = tool.execute_with_audit(
        db=in_memory_db,
        start_time=now - timedelta(minutes=15),
        end_time=now,
        limit=10,
    )
    assert resp2.success is True
    assert resp2.audit.record_count == 1
    anomalies = resp2.data["anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["service_name"] == "payment-service"
    assert anomalies[0]["id"].startswith("ev-anomaly-payment-service-")


def test_get_anomaly_events_invalid_window(in_memory_db: Session) -> None:
    """Test validation rejection when end_time <= start_time or exceeds 4h."""
    tool = GetAnomalyEventsTool()
    now = datetime.now(UTC)

    # Reversed window
    resp = tool.execute_with_audit(
        db=in_memory_db,
        start_time=now,
        end_time=now - timedelta(minutes=5),
    )
    assert resp.success is False
    assert resp.audit.error_category == "INVALID_ARGUMENTS"

    # Window > 4h
    resp2 = tool.execute_with_audit(
        db=in_memory_db,
        start_time=now - timedelta(hours=5),
        end_time=now,
    )
    assert resp2.success is False
    assert "exceeds maximum permitted span" in resp2.error


def test_query_service_metrics(in_memory_db: Session) -> None:
    """Test metric query aggregations and evidence formulation."""
    tool = QueryServiceMetricsTool()
    now = datetime.now(UTC)

    # Insert metrics
    for val in [100.0, 150.0, 200.0]:
        in_memory_db.add(
            MetricRecordModel(
                timestamp=now - timedelta(minutes=2),
                service_name="order-service",
                metric_name="http_duration_ms",
                value=val,
                unit="ms",
            )
        )
    in_memory_db.commit()

    resp = tool.execute_with_audit(
        db=in_memory_db,
        service_name="order-service",
        start_time=now - timedelta(minutes=10),
        end_time=now,
    )
    assert resp.success is True
    assert resp.data["service_name"] == "order-service"
    stats = resp.data["metrics"]["http_duration_ms"]
    assert stats["count"] == 3
    assert stats["min"] == 100.0
    assert stats["max"] == 200.0
    assert stats["avg"] == 150.0
    assert len(resp.data["evidence"]) == 1


def test_query_service_logs(in_memory_db: Session) -> None:
    """Test log queries and error severity evidence extraction."""
    tool = QueryServiceLogsTool()
    now = datetime.now(UTC)

    in_memory_db.add(
        LogRecordModel(
            timestamp=now - timedelta(minutes=1),
            service_name="payment-service",
            severity="ERROR",
            message="ConnectionPoolTimeout: Unable to acquire connection",
            exception_type="ConnectionPoolTimeout",
        )
    )
    in_memory_db.commit()

    resp = tool.execute_with_audit(
        db=in_memory_db,
        service_name="payment-service",
        start_time=now - timedelta(minutes=10),
        end_time=now,
        severity="ERROR",
    )
    assert resp.success is True
    assert len(resp.data["logs"]) == 1
    assert len(resp.data["evidence"]) == 1
    assert resp.data["evidence"][0]["id"].startswith("ev-log-payment-service-")


def test_query_trace_spans(in_memory_db: Session) -> None:
    """Test trace span querying and error span evidence extraction."""
    tool = QueryTraceSpansTool()
    now = datetime.now(UTC)

    in_memory_db.add(
        TraceSpanModel(
            trace_id="trace-001",
            span_id="span-001",
            service_name="payment-service",
            operation_name="process_payment",
            start_time=now - timedelta(minutes=2),
            end_time=now - timedelta(minutes=1),
            duration_ms=650.0,
            status_code="ERROR",
        )
    )
    in_memory_db.commit()

    resp = tool.execute_with_audit(
        db=in_memory_db,
        service_name="payment-service",
        start_time=now - timedelta(minutes=10),
        end_time=now,
    )
    assert resp.success is True
    assert resp.data["total_count"] == 1
    assert len(resp.data["evidence"]) == 1


def test_get_service_topology(in_memory_db: Session) -> None:
    """Test topology tool returns service snapshot and caller/downstream edges."""
    tool = GetServiceTopologyTool()
    now = datetime.now(UTC)

    snap = TopologySnapshotModel(
        id="topo-test-1",
        version="v1.0.0",
        data_source="test",
        time_window_start=now - timedelta(minutes=30),
        time_window_end=now,
        service_count=2,
        edge_count=1,
        services_json=["order-service", "payment-service"],
    )
    edge = TopologyEdgeModel(
        topology_id="topo-test-1",
        caller_service="order-service",
        downstream_service="payment-service",
        call_count=100,
        error_count=5,
        error_rate=0.05,
        latency_p95_ms=820.0,
        first_seen=now - timedelta(minutes=20),
        last_seen=now,
    )
    in_memory_db.add(snap)
    in_memory_db.add(edge)
    in_memory_db.commit()

    resp = tool.execute_with_audit(db=in_memory_db)
    assert resp.success is True
    assert resp.data["snapshot_id"] == "topo-test-1"
    assert len(resp.data["edges"]) == 1
    assert len(resp.data["evidence"]) == 1


def test_retrieve_runbook_tool() -> None:
    """Test local runbook retrieval with keyword match."""
    tool = RetrieveRunbookTool()

    resp = tool.execute_with_audit(
        query="payment latency slowdown timeout",
        target_service="payment-service",
        limit=2,
    )
    assert resp.success is True
    assert len(resp.data["runbooks"]) > 0
    assert resp.data["runbooks"][0]["target_service"] == "payment-service"
    assert len(resp.data["evidence"]) > 0
    assert resp.data["evidence"][0]["id"].startswith("ev-runbook-payment-service-")


def test_evaluate_remediation_policy_tool() -> None:
    """Test policy evaluation tool enforces allowlists and returns structured decisions."""
    tool = EvaluateRemediationPolicyTool()

    # Allowed safe action
    resp_allow = tool.execute_with_audit(
        action_type="restore_service",
        target_service="payment-service",
        supporting_citation_ids=["ev-test-1"],
    )
    assert resp_allow.success is True
    assert resp_allow.data["decision"] in ("ALLOW", "REQUIRES_APPROVAL")

    # Prohibited service
    resp_deny = tool.execute_with_audit(
        action_type="restore_service",
        target_service="unauthorized-core-db",
        supporting_citation_ids=["ev-test-1"],
    )
    assert resp_deny.success is True
    assert resp_deny.data["decision"] == "DENY"
    assert "prohibited" in resp_deny.data["violations"][0].lower()


def test_execute_simulated_remediation_tool() -> None:
    """Test simulated remediation execution against controlled fault engine."""
    fault_engine.reset()
    # Activate controlled fault
    fault_engine.activate_fault(
        experiment_id="exp-tool-test",
        fault_type="latency_spike",
        target_service="payment-service",
        duration_seconds=30,
    )
    assert fault_engine.get_active_fault_for_service("payment-service") is not None

    tool = ExecuteSimulatedRemediationTool()

    # Execute simulation
    resp = tool.execute_with_audit(
        action_type="stop_fault_scenario",
        target_service="payment-service",
        parameters={"experiment_id": "exp-tool-test"},
        is_approved=True,
        supporting_citation_ids=["ev-citation-1"],
    )
    assert resp.success is True
    assert resp.data["status"] == "SIMULATED_SUCCESS"
    assert resp.data["state_verified"] is True
    assert fault_engine.get_active_fault_for_service("payment-service") is None

    fault_engine.reset()
