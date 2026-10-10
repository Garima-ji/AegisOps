"""Unit tests for schema serialization and validation."""

from datetime import UTC, datetime

from app.schemas.common import (
    HealthResponse,
    ProblemDetails,
    ReadinessResponse,
    SystemInfoResponse,
)


def test_health_response_schema() -> None:
    """Verify HealthResponse schema defaults and serialization."""
    now = datetime.now(UTC)
    res = HealthResponse(version="0.1.0", timestamp=now)
    assert res.status == "alive"
    assert res.version == "0.1.0"
    assert res.timestamp == now

    data = res.model_dump()
    assert data["status"] == "alive"


def test_readiness_response_schema() -> None:
    """Verify ReadinessResponse schema validation."""
    res = ReadinessResponse(
        status="ready",
        database="connected",
        details={"latency_ms": 1.25},
    )
    assert res.status == "ready"
    assert res.database == "connected"
    assert res.details["latency_ms"] == 1.25


def test_system_info_response_schema() -> None:
    """Verify SystemInfoResponse serialization."""
    res = SystemInfoResponse(
        app_name="AegisOps",
        version="0.1.0",
        environment="development",
        debug=False,
        api_prefix="/api/v1",
        features={"enable_metrics": True},
    )
    assert res.app_name == "AegisOps"
    assert res.features["enable_metrics"] is True


def test_problem_details_rfc7807_compliance() -> None:
    """Verify ProblemDetails RFC 7807 fields."""
    problem = ProblemDetails(
        title="Bad Request",
        status=400,
        detail="Missing required header",
        instance="/api/v1/test",
        code="HEADER_MISSING",
    )
    assert problem.title == "Bad Request"
    assert problem.status == 400
    assert problem.code == "HEADER_MISSING"
    assert problem.type == "about:blank"
