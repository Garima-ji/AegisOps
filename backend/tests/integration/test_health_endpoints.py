"""Integration tests for health liveness and readiness endpoints."""

from unittest.mock import patch

from fastapi.testclient import TestClient


def test_liveness_probe_returns_200(client: TestClient) -> None:
    """Verify /health/live returns HTTP 200 with process liveness."""
    response = client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"
    assert "timestamp" in data
    assert "version" in data


def test_liveness_probe_under_api_v1(client: TestClient) -> None:
    """Verify /api/v1/health/live also works consistently."""
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_probe_healthy(client: TestClient) -> None:
    """Verify /health/ready returns HTTP 200 when database is healthy."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"
    assert "details" in data


def test_readiness_probe_degraded_returns_503(client: TestClient) -> None:
    """Verify /health/ready returns HTTP 503 when database is unreachable."""
    with patch(
        "app.api.v1.health.check_database_connection",
        return_value=(False, "Connection refused: database down", {"dialect": "postgresql"}),
    ):
        response = client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"] == "disconnected"
        assert "Connection refused" in data["details"]["database"]["message"]
