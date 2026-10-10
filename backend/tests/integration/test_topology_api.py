"""Integration tests for Phase 4 service topology FastAPI endpoints."""

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.telemetry import TraceSpanModel
from app.db.models.topology import TopologyEdgeModel, TopologySnapshotModel


def _hex_trace(id_str: str) -> str:
    return hashlib.md5(id_str.encode()).hexdigest()


def _hex_span(id_str: str) -> str:
    return hashlib.md5(id_str.encode()).hexdigest()[:16]


def test_rebuild_topology_endpoint_success(client: TestClient, test_db_session: Session) -> None:
    """Verify on-demand topology reconstruction from database trace spans."""
    now = datetime.now(UTC) - timedelta(minutes=5)
    tr_id = _hex_trace("tr-rebuild-1")
    parent_id = _hex_span("sp-parent")
    child_id = _hex_span("sp-child")

    span_parent = TraceSpanModel(
        trace_id=tr_id,
        span_id=parent_id,
        parent_span_id=None,
        service_name="checkout-service",
        operation_name="checkout_order",
        start_time=now,
        end_time=now + timedelta(milliseconds=120),
        duration_ms=120.0,
        status_code="OK",
    )
    span_child = TraceSpanModel(
        trace_id=tr_id,
        span_id=child_id,
        parent_span_id=parent_id,
        service_name="payment-service",
        operation_name="process_payment",
        start_time=now + timedelta(milliseconds=10),
        end_time=now + timedelta(milliseconds=80),
        duration_ms=70.0,
        status_code="OK",
    )
    test_db_session.add_all([span_parent, span_child])
    test_db_session.commit()

    payload = {
        "lookback_minutes": 60,
        "version": "topo-test-v1",
        "data_source": "trace_spans",
    }
    resp = client.post("/api/v1/topology/rebuild", json=payload)
    assert resp.status_code == status.HTTP_201_CREATED

    body = resp.json()
    assert body["version"] == "topo-test-v1"
    assert body["service_count"] == 2
    assert body["edge_count"] == 1
    assert "checkout-service" in body["services"]
    assert "payment-service" in body["services"]

    edge = body["edges"][0]
    assert edge["caller_service"] == "checkout-service"
    assert edge["downstream_service"] == "payment-service"
    assert edge["call_count"] == 1


def test_rebuild_topology_no_spans_returns_400(client: TestClient) -> None:
    """Verify rebuilding topology on empty trace spans returns 400 Bad Request."""
    resp = client.post(
        "/api/v1/topology/rebuild",
        json={"lookback_minutes": 15},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "No trace spans observed" in resp.json()["detail"]


def test_get_topology_latest_and_by_identifier(
    client: TestClient, test_db_session: Session
) -> None:
    """Verify querying latest topology and specific snapshot by identifier."""
    # Seed snapshot in DB
    now = datetime.now(UTC)
    snap = TopologySnapshotModel(
        id="topo-snap-test-01",
        version="v1.0.0",
        data_source="trace_spans",
        time_window_start=now - timedelta(hours=1),
        time_window_end=now,
        service_count=2,
        edge_count=1,
        services_json=["service-a", "service-b"],
        created_at=now,
    )
    edge = TopologyEdgeModel(
        topology_id="topo-snap-test-01",
        caller_service="service-a",
        downstream_service="service-b",
        call_count=10,
        error_count=0,
        error_rate=0.0,
        first_seen=now - timedelta(hours=1),
        last_seen=now,
    )
    test_db_session.add_all([snap, edge])
    test_db_session.commit()

    # 1. Query latest
    resp_latest = client.get("/api/v1/topology")
    assert resp_latest.status_code == status.HTTP_200_OK
    assert resp_latest.json()["snapshot_id"] == "topo-snap-test-01"

    # 2. Query by ID
    resp_id = client.get("/api/v1/topology?identifier=topo-snap-test-01")
    assert resp_id.status_code == status.HTTP_200_OK
    assert resp_id.json()["snapshot_id"] == "topo-snap-test-01"

    # 3. Query nonexistent
    resp_none = client.get("/api/v1/topology?identifier=nonexistent-snap")
    assert resp_none.status_code == status.HTTP_404_NOT_FOUND


def test_list_topology_snapshots(client: TestClient, test_db_session: Session) -> None:
    """Verify paginated listing of historical topology snapshots."""
    now = datetime.now(UTC)
    snap = TopologySnapshotModel(
        id="topo-snap-list-01",
        version="v1.0.1",
        data_source="trace_spans",
        time_window_start=now - timedelta(hours=1),
        time_window_end=now,
        service_count=3,
        edge_count=2,
        services_json=["a", "b", "c"],
        created_at=now,
    )
    test_db_session.add(snap)
    test_db_session.commit()

    resp = client.get("/api/v1/topology/snapshots?limit=10&offset=0")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] >= 1
    assert any(s["snapshot_id"] == "topo-snap-list-01" for s in body["items"])


def test_get_service_dependencies(client: TestClient, test_db_session: Session) -> None:
    """Verify retrieving callers and downstream dependencies for a specific service."""
    now = datetime.now(UTC)
    snap = TopologySnapshotModel(
        id="topo-snap-deps-01",
        version="v1.0.2",
        data_source="trace_spans",
        time_window_start=now - timedelta(hours=1),
        time_window_end=now,
        service_count=3,
        edge_count=2,
        services_json=["frontend", "checkout", "payment"],
        created_at=now,
    )
    # frontend -> checkout -> payment
    edge1 = TopologyEdgeModel(
        topology_id="topo-snap-deps-01",
        caller_service="frontend",
        downstream_service="checkout",
        call_count=50,
        first_seen=now,
        last_seen=now,
    )
    edge2 = TopologyEdgeModel(
        topology_id="topo-snap-deps-01",
        caller_service="checkout",
        downstream_service="payment",
        call_count=45,
        first_seen=now,
        last_seen=now,
    )
    test_db_session.add_all([snap, edge1, edge2])
    test_db_session.commit()

    # Query dependencies for checkout
    resp = client.get(
        "/api/v1/topology/services/checkout/dependencies?topology_id=topo-snap-deps-01"
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["service_name"] == "checkout"
    assert body["total_callers"] == 1
    assert body["direct_callers"][0]["caller_service"] == "frontend"
    assert body["total_downstream"] == 1
    assert body["direct_downstream"][0]["downstream_service"] == "payment"
