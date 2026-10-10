"""API endpoints for microservice inventory and registration tracking."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.service import ServiceListResponse, ServiceSchema
from app.services.registry_service import get_service_by_name, list_services

router = APIRouter(prefix="/services", tags=["Services"])


@router.get(
    "",
    response_model=ServiceListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Registered Microservices",
    description="Returns all actively registered microservices and their operational metadata.",
)
def get_services(
    environment: str | None = Query(default=None, description="Filter by environment"),
    limit: int = Query(default=100, ge=1, le=1000, description="Pagination limit"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    session: Session = Depends(get_db),
) -> ServiceListResponse:
    """List registered services discovered across telemetry ingestion pipelines."""
    models, total = list_services(
        session=session,
        environment=environment,
        limit=limit,
        offset=offset,
    )
    items = [
        ServiceSchema(
            name=m.name,
            environment=m.environment,
            version=m.version,
            status=m.status,  # type: ignore[arg-type]
            first_seen=m.first_seen,
            last_seen=m.last_seen,
            metadata=m.metadata_json or {},
        )
        for m in models
    ]
    return ServiceListResponse(items=items, total=total)


@router.get(
    "/{service_name}",
    response_model=ServiceSchema,
    status_code=status.HTTP_200_OK,
    summary="Get Service Registration Details",
    description="Retrieve registration status and metadata for a specific microservice.",
)
def get_service(
    service_name: str,
    environment: str | None = Query(default=None, description="Optional environment qualifier"),
    session: Session = Depends(get_db),
) -> ServiceSchema:
    """Retrieve details for a single registered service."""
    model = get_service_by_name(session=session, service_name=service_name, environment=environment)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_name}' not found in registry.",
        )
    return ServiceSchema(
        name=model.name,
        environment=model.environment,
        version=model.version,
        status=model.status,  # type: ignore[arg-type]
        first_seen=model.first_seen,
        last_seen=model.last_seen,
        metadata=model.metadata_json or {},
    )
