"""Integration tests for system metadata endpoint."""

from fastapi.testclient import TestClient


def test_system_info_endpoint(client: TestClient) -> None:
    """Verify /api/v1/system/info returns metadata without exposing secrets."""
    response = client.get("/api/v1/system/info")
    assert response.status_code == 200
    data = response.json()

    assert data["app_name"] == "AegisOps"
    assert data["version"] == "0.1.0"
    assert data["api_prefix"] == "/api/v1"
    assert "features" in data
    assert "enable_metrics" in data["features"]

    # Verify sensitive attributes are absent
    raw_text = response.text
    assert "secret_key" not in raw_text
    assert "database_url" not in raw_text
    assert "password" not in raw_text
