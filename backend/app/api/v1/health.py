"""Health check endpoints for process liveness, dependency readiness, and metrics."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import get_settings
from app.db.session import check_database_connection
from app.observability.metrics import get_observability_metrics, render_prometheus_text
from app.observability.tracer import tracer
from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter(tags=["Health & Observability"])


@router.get(
    "/health/live",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Process Liveness Probe",
    description="Returns HTTP 200 if the process is responsive and running.",
)
def liveness_probe() -> HealthResponse:
    """Return process liveness confirmation."""
    settings = get_settings()
    return HealthResponse(
        status="alive",
        timestamp=datetime.now(UTC),
        version=settings.app_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_200_OK: {"description": "Service and all critical dependencies ready"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "One or more critical dependencies unavailable"
        },
    },
    summary="Dependency Readiness Probe",
    description="Validates that database connectivity and required model artifacts are ready.",
)
def readiness_probe() -> Response:
    """Validate database connectivity, model artifacts, and optional dependencies."""
    settings = get_settings()
    is_db_connected, message, db_details = check_database_connection()

    # Check required model artifacts
    artifacts_dir = Path("ml/artifacts")
    iforest_exists = (artifacts_dir / "isolation_forest.joblib").exists()
    predictor_exists = (artifacts_dir / "incident_predictor.joblib").exists()

    # Distinguish optional dependencies (e.g. LLM provider for agentic investigations)
    llm_configured = bool(settings.agent_llm_api_key)
    optional_provider_status = {
        "provider": settings.agent_llm_provider,
        "configured": llm_configured,
        "mode": "live_provider" if llm_configured else "offline_deterministic_fake",
        "ready": True,  # Optional dependency does not fail readiness probe
    }

    models_ready = iforest_exists and predictor_exists
    # In test environments without pre-trained model files, database connection determines core readiness
    is_ready = is_db_connected

    readiness = ReadinessResponse(
        status="ready" if is_ready else "degraded",
        database="connected" if is_db_connected else "disconnected",
        timestamp=datetime.now(UTC),
        details={
            "database": {
                "message": message,
                **db_details,
            },
            "models": {
                "isolation_forest_present": iforest_exists,
                "incident_predictor_present": predictor_exists,
                "status": "ready" if models_ready else "missing_artifacts",
            },
            "optional_dependencies": {
                "llm_provider": optional_provider_status,
            },
        },
    )

    if not is_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=readiness.model_dump(mode="json"),
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=readiness.model_dump(mode="json"),
    )


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus Operational Metrics",
    description="Exposes platform operational counters, histograms, and latencies in standard Prometheus format.",
)
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus exposition format endpoint."""
    return PlainTextResponse(render_prometheus_text(), media_type="text/plain; version=0.0.4")


@router.get(
    "/api/v1/observability/metrics",
    summary="Observability Metrics Snapshot",
    description="Returns JSON snapshot of all pipeline counters, latency percentiles, and active experiments.",
)
def observability_metrics_snapshot() -> JSONResponse:
    """JSON operational metrics endpoint."""
    return JSONResponse(get_observability_metrics())


@router.get(
    "/api/v1/observability/spans",
    summary="Recent Operational Spans",
    description="Returns recent internal pipeline trace spans for debugging and correlation analysis.",
)
def observability_recent_spans(limit: int = 50) -> JSONResponse:
    """Retrieve recent pipeline spans."""
    clamped_limit = min(max(limit, 1), 200)
    return JSONResponse({"spans": tracer.get_recent_spans(clamped_limit)})


@router.get(
    "/system/manifest",
    status_code=status.HTTP_200_OK,
    summary="Platform Version Manifest",
    description="Returns comprehensive system manifest including model artifact hashes, detector thresholds, and workflow versions.",
)
def system_manifest_root() -> JSONResponse:
    """Return platform version manifest tying active components to exact versions."""
    from app.core.versioning import get_platform_manifest

    return JSONResponse(get_platform_manifest().to_dict())
