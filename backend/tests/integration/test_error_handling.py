"""Integration tests for RFC 7807 error handling and middleware."""

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.exceptions import PolicyViolationError, ResourceNotFoundError
from app.main import create_app

# Router for testing error conditions
error_test_router = APIRouter(prefix="/test-errors")


@error_test_router.get("/policy-violation")
def trigger_policy_violation() -> None:
    raise PolicyViolationError(
        "Remediation action 'DeleteAllNodes' is not permit-listed",
        details={"action": "DeleteAllNodes"},
    )


@error_test_router.get("/resource-not-found")
def trigger_resource_not_found() -> None:
    raise ResourceNotFoundError("Incident #9999 was not found")


@error_test_router.get("/unexpected-crash")
def trigger_unexpected_crash() -> None:
    raise RuntimeError("Simulated unexpected failure")


def test_404_returns_rfc7807_problem_details(client: TestClient) -> None:
    """Verify nonexistent URL returns RFC 7807 compliant problem details."""
    response = client.get("/nonexistent/endpoint/path")
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == 404
    assert data["code"] == "HTTP_404"
    assert "detail" in data
    assert "timestamp" in data


def test_correlation_id_header_injected(client: TestClient) -> None:
    """Verify X-Request-ID header is generated and returned."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"].startswith("req_")


def test_custom_correlation_id_propagated(client: TestClient) -> None:
    """Verify custom X-Request-ID is preserved and echoed."""
    custom_id = "custom_trace_987654"
    response = client.get("/health/live", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_id


def test_domain_exception_handled_as_problem_details() -> None:
    """Verify domain exceptions serialize to ProblemDetails with status and code."""
    app = create_app()
    app.include_router(error_test_router)
    with TestClient(app) as test_client:
        response = test_client.get("/test-errors/policy-violation")
        assert response.status_code == 403
        data = response.json()
        assert data["status"] == 403
        assert data["code"] == "POLICY_VIOLATION"
        assert "not permit-listed" in data["detail"]
        assert data["instance"] == "/test-errors/policy-violation"


def test_generic_500_exception_handled_safely() -> None:
    """Verify unhandled 500 error returns RFC 7807 details without leaking internal crash details in non-debug mode."""
    app = create_app()
    app.include_router(error_test_router)
    with TestClient(app) as test_client:
        response = test_client.get("/test-errors/unexpected-crash")
        assert response.status_code == 500
        data = response.json()
        assert data["status"] == 500
        assert data["code"] == "INTERNAL_SERVER_ERROR"
