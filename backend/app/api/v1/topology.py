"""FastAPI endpoints for service topology extraction, snapshots, and dependency queries."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.topology import (
    ServiceDependenciesResponse,
    TopologyEdgeSchema,
    TopologyRebuildRequest,
    TopologySnapshotListResponse,
    TopologySnapshotResponse,
    TopologySnapshotSummary,
)
from app.topology.models import _to_utc
from app.topology.service import TopologyService

logger = logging.getLogger("aegisops.topology.api")

router = APIRouter(prefix="/topology", tags=["Service Topology"])


@router.post(
    "/rebuild",
    response_model=TopologySnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Rebuild Topology Snapshot",
    description="Extracts directed cross-service dependency edges from observed trace spans across the observation window.",
)
def rebuild_topology(
    payload: TopologyRebuildRequest,
    session: Session = Depends(get_db),
) -> TopologySnapshotResponse:
    """Trigger on-demand reconstruction of service topology graph from distributed traces."""
    now = datetime.now(UTC)
    end_time = _to_utc(payload.end_time) if payload.end_time else now
    start_time = (
        _to_utc(payload.start_time)
        if payload.start_time
        else (end_time - timedelta(minutes=payload.lookback_minutes))
    )

    try:
        snapshot = TopologyService.rebuild_topology(
            session=session,
            start_time=start_time,
            end_time=end_time,
            version=payload.version,
            data_source=payload.data_source,
        )
        edges = TopologyService.get_edges_for_snapshot(session, snapshot.id)

        edge_schemas = [
            TopologyEdgeSchema(
                caller_service=e.caller_service,
                downstream_service=e.downstream_service,
                call_count=e.call_count,
                error_count=e.error_count,
                error_rate=e.error_rate,
                latency_p50_ms=e.latency_p50_ms,
                latency_p95_ms=e.latency_p95_ms,
                first_seen=e.first_seen,
                last_seen=e.last_seen,
                is_observed=e.is_observed,
                is_manual=e.is_manual,
                metadata=e.metadata_json,
            )
            for e in edges
        ]

        return TopologySnapshotResponse(
            snapshot_id=snapshot.id,
            version=snapshot.version,
            data_source=snapshot.data_source,
            time_window_start=snapshot.time_window_start,
            time_window_end=snapshot.time_window_end,
            service_count=snapshot.service_count,
            edge_count=snapshot.edge_count,
            services=snapshot.services_json or [],
            edges=edge_schemas,
            metadata=snapshot.metadata_json,
            created_at=snapshot.created_at,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as exc:
        logger.exception("Failed to rebuild service topology: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rebuild topology graph from trace spans.",
        ) from exc


@router.get(
    "",
    response_model=TopologySnapshotResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Active or Specific Topology",
    description="Retrieve the latest topology snapshot or query a specific snapshot by ID or version.",
)
def get_topology(
    identifier: str | None = Query(
        default=None,
        description="Optional snapshot ID or version string (returns latest if omitted)",
    ),
    session: Session = Depends(get_db),
) -> TopologySnapshotResponse:
    """Retrieve full service topology graph representation."""
    if identifier:
        snapshot = TopologyService.get_topology_by_id_or_version(session, identifier)
    else:
        snapshot = TopologyService.get_latest_topology(session)

    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No topology snapshot found. Rebuild topology using POST /api/v1/topology/rebuild first.",
        )

    edges = TopologyService.get_edges_for_snapshot(session, snapshot.id)

    edge_schemas = [
        TopologyEdgeSchema(
            caller_service=e.caller_service,
            downstream_service=e.downstream_service,
            call_count=e.call_count,
            error_count=e.error_count,
            error_rate=e.error_rate,
            latency_p50_ms=e.latency_p50_ms,
            latency_p95_ms=e.latency_p95_ms,
            first_seen=e.first_seen,
            last_seen=e.last_seen,
            is_observed=e.is_observed,
            is_manual=e.is_manual,
            metadata=e.metadata_json,
        )
        for e in edges
    ]

    return TopologySnapshotResponse(
        snapshot_id=snapshot.id,
        version=snapshot.version,
        data_source=snapshot.data_source,
        time_window_start=snapshot.time_window_start,
        time_window_end=snapshot.time_window_end,
        service_count=snapshot.service_count,
        edge_count=snapshot.edge_count,
        services=snapshot.services_json or [],
        edges=edge_schemas,
        metadata=snapshot.metadata_json,
        created_at=snapshot.created_at,
    )


@router.get(
    "/snapshots",
    response_model=TopologySnapshotListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Topology Snapshots",
    description="List all generated topology versions and time ranges with pagination.",
)
def list_topology_snapshots(
    limit: int = Query(default=20, ge=1, le=100, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> TopologySnapshotListResponse:
    """List historical topology snapshot records."""
    total, snapshots = TopologyService.list_snapshots(session, limit=limit, offset=offset)

    items = [
        TopologySnapshotSummary(
            snapshot_id=s.id,
            version=s.version,
            data_source=s.data_source,
            time_window_start=s.time_window_start,
            time_window_end=s.time_window_end,
            service_count=s.service_count,
            edge_count=s.edge_count,
            created_at=s.created_at,
        )
        for s in snapshots
    ]

    return TopologySnapshotListResponse(
        total=total,
        items=items,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/services/{service_name}/dependencies",
    response_model=ServiceDependenciesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Service Dependencies",
    description="Query direct callers and downstream dependencies for a given service.",
)
def get_service_dependencies(
    service_name: str,
    topology_id: str | None = Query(default=None, description="Optional topology snapshot ID"),
    session: Session = Depends(get_db),
) -> ServiceDependenciesResponse:
    """Retrieve direct upstream and downstream dependency edges for a service."""
    try:
        deps = TopologyService.get_service_dependencies(
            session=session,
            service_name=service_name,
            topology_id=topology_id,
        )
        return ServiceDependenciesResponse(**deps)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
