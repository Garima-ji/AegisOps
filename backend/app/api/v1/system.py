"""System information and runtime metadata endpoints."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.versioning import get_platform_manifest
from app.schemas.common import SystemInfoResponse

router = APIRouter(tags=["System"])


@router.get(
    "/system/info",
    response_model=SystemInfoResponse,
    status_code=status.HTTP_200_OK,
    summary="Application Metadata",
    description="Returns sanitized application metadata, deployment environment, and feature flags.",
)
def get_system_info() -> SystemInfoResponse:
    """Return sanitized system metadata without exposing sensitive credentials."""
    settings = get_settings()
    return SystemInfoResponse(
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        debug=settings.debug,
        api_prefix=settings.api_v1_prefix,
        features={
            "enable_metrics": settings.enable_metrics,
            "enable_background_tasks": settings.enable_background_tasks,
        },
    )


@router.get(
    "/system/manifest",
    status_code=status.HTTP_200_OK,
    summary="Platform Version Manifest",
    description="Returns comprehensive system manifest including model artifact hashes, detector thresholds, and workflow versions.",
)
def get_system_manifest_endpoint() -> JSONResponse:
    """Return platform version manifest tying active components to exact versions."""
    manifest = get_platform_manifest()
    return JSONResponse(manifest.to_dict())
