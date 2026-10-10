"""Integration tests for fault injection experiments and ground-truth labels APIs."""

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_create_experiment_and_ground_truth_labels(
    client: TestClient, test_db_session: Session
) -> None:
    """Creating an experiment must register state, seed ground-truth labels, and return 201."""
    payload = {
        "scenario_name": "payment_latency_spike",
        "description": "Simulates payment processing degradation",
        "target_service": "payment-service",
        "fault_type": "latency_spike",
        "parameters": {"added_latency_ms": 750.0},
        "duration_seconds": 30,
        "initiating_service": "payment-service",
        "symptom_services": ["order-service"],
    }

    resp = client.post("/api/v1/experiments", json=payload)
    assert resp.status_code == status.HTTP_201_CREATED
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert body["target_service"] == "payment-service"
    exp_id = body["id"]

    # 1. Fetch experiment by ID
    resp_get = client.get(f"/api/v1/experiments/{exp_id}")
    assert resp_get.status_code == status.HTTP_200_OK
    assert resp_get.json()["id"] == exp_id

    # 2. Fetch ground-truth labels
    resp_labels = client.get(f"/api/v1/experiments/{exp_id}/labels")
    assert resp_labels.status_code == status.HTTP_200_OK
    labels_body = resp_labels.json()
    assert labels_body["experiment_id"] == exp_id
    assert labels_body["total"] >= 2  # Initiator + symptom service

    initiator_label = next(
        lbl for lbl in labels_body["labels"] if lbl["service_name"] == "payment-service"
    )
    assert initiator_label["is_fault"] is True
    assert initiator_label["severity"] == "CRITICAL"
    assert initiator_label["metadata"]["role"] == "initiator"

    symptom_label = next(
        lbl for lbl in labels_body["labels"] if lbl["service_name"] == "order-service"
    )
    assert symptom_label["is_fault"] is True
    assert symptom_label["metadata"]["role"] == "symptom"

    # 3. Stop experiment safely
    resp_stop = client.post(f"/api/v1/experiments/{exp_id}/stop")
    assert resp_stop.status_code == status.HTTP_200_OK
    stop_body = resp_stop.json()
    assert stop_body["status"] == "STOPPED"
    assert "Cleaned up" in stop_body["cleanup_result"]


def test_create_experiment_rejects_non_allowlisted_service(client: TestClient) -> None:
    """Target services outside the local disposable test set must be rejected."""
    payload = {
        "scenario_name": "attack_arbitrary_host",
        "target_service": "production-core-auth.net",
        "fault_type": "latency_spike",
        "duration_seconds": 30,
        "initiating_service": "production-core-auth.net",
    }
    resp = client.post("/api/v1/experiments", json=payload)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    problem = resp.json()
    assert "safety allowlist" in problem["detail"]


def test_list_experiments_endpoint(client: TestClient) -> None:
    """GET /api/v1/experiments returns list of created experiments."""
    client.post(
        "/api/v1/experiments",
        json={
            "scenario_name": "test_exp",
            "target_service": "order-service",
            "fault_type": "error_surge",
            "duration_seconds": 20,
            "initiating_service": "order-service",
        },
    )

    resp = client.get("/api/v1/experiments?limit=10")
    assert resp.status_code == status.HTTP_200_OK
    items = resp.json()
    assert len(items) >= 1
    assert items[0]["target_service"] == "order-service"
