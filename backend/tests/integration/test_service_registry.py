"""Integration tests for service registry and idempotent upserts."""

from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.telemetry import ServiceModel


def test_service_auto_registration_and_idempotence(
    client: TestClient, test_db_session: Session
) -> None:
    """Repeated telemetry from a service refreshes last_seen without creating duplicates."""
    now = datetime.now(UTC)

    # 1. Ingest first point
    payload1 = {
        "items": [
            {
                "timestamp": now.isoformat(),
                "service_name": "notification-service",
                "metric_name": "messages.sent",
                "value": 1.0,
            }
        ]
    }
    client.post("/api/v1/telemetry/metrics", json=payload1)

    services_after_first = list(
        test_db_session.execute(
            select(ServiceModel).where(ServiceModel.name == "notification-service")
        )
        .scalars()
        .all()
    )
    assert len(services_after_first) == 1
    first_last_seen = services_after_first[0].last_seen

    # 2. Ingest second point 5 seconds later
    later = now + timedelta(seconds=5)
    payload2 = {
        "items": [
            {
                "timestamp": later.isoformat(),
                "service_name": "notification-service",
                "metric_name": "messages.sent",
                "value": 2.0,
            }
        ]
    }
    client.post("/api/v1/telemetry/metrics", json=payload2)

    # Still exactly 1 row, but last_seen has been refreshed
    services_after_second = list(
        test_db_session.execute(
            select(ServiceModel).where(ServiceModel.name == "notification-service")
        )
        .scalars()
        .all()
    )
    assert len(services_after_second) == 1
    t1 = (
        first_last_seen
        if first_last_seen.tzinfo is not None
        else first_last_seen.replace(tzinfo=UTC)
    )
    t2 = (
        services_after_second[0].last_seen
        if services_after_second[0].last_seen.tzinfo is not None
        else services_after_second[0].last_seen.replace(tzinfo=UTC)
    )
    assert t2 >= t1


def test_list_and_get_service_endpoints(client: TestClient, test_db_session: Session) -> None:
    """Verify GET /services and GET /services/{name} endpoints."""
    # Seed telemetry for two services
    client.post(
        "/api/v1/telemetry/metrics",
        json={
            "items": [
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "service_name": "order-service",
                    "metric_name": "reqs",
                    "value": 1.0,
                },
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "service_name": "payment-service",
                    "metric_name": "reqs",
                    "value": 1.0,
                },
            ]
        },
    )

    # List services
    resp = client.get("/api/v1/services")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] == 2
    service_names = {s["name"] for s in body["items"]}
    assert "order-service" in service_names
    assert "payment-service" in service_names

    # Get specific service
    resp_single = client.get("/api/v1/services/order-service")
    assert resp_single.status_code == status.HTTP_200_OK
    single_body = resp_single.json()
    assert single_body["name"] == "order-service"
    assert single_body["status"] == "healthy"

    # Non-existent service
    resp_404 = client.get("/api/v1/services/nonexistent-service")
    assert resp_404.status_code == status.HTTP_404_NOT_FOUND
