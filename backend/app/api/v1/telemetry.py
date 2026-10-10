"""API endpoints for telemetry ingestion, querying, and pipeline operational metrics."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.observability.metrics import get_metrics_snapshot
from app.schemas.telemetry import (
    IngestionResponse,
    LogBatchRequest,
    LogListResponse,
    LogRecordSchema,
    MetricBatchRequest,
    MetricListResponse,
    MetricPoint,
    PipelineMetricsResponse,
    SpanRecordSchema,
    TraceBatchRequest,
    TraceListResponse,
    TraceWaterfallResponse,
)
from app.services.telemetry_service import TelemetryService

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


# -----------------------------------------------------------------------------
# Ingestion Endpoints
# -----------------------------------------------------------------------------


@router.post(
    "/metrics",
    response_model=IngestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest Bounded Metric Batch",
    description="Validates, normalizes, registers service identities, and persists metric observations.",
)
def ingest_metrics(
    payload: MetricBatchRequest,
    session: Session = Depends(get_db),
) -> IngestionResponse:
    """Ingest a batch of metric data points."""
    return TelemetryService.ingest_metrics(session=session, items=payload.items)


@router.post(
    "/logs",
    response_model=IngestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest Bounded Structured Log Batch",
    description="Validates, sanitizes messages, bounds attributes, and persists structured logs.",
)
def ingest_logs(
    payload: LogBatchRequest,
    session: Session = Depends(get_db),
) -> IngestionResponse:
    """Ingest a batch of structured log events."""
    return TelemetryService.ingest_logs(session=session, items=payload.items)


@router.post(
    "/traces",
    response_model=IngestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest Bounded Distributed Trace Spans",
    description="Validates OpenTelemetry span semantics, duration bounds, and persists spans.",
)
def ingest_traces(
    payload: TraceBatchRequest,
    session: Session = Depends(get_db),
) -> IngestionResponse:
    """Ingest a batch of distributed trace span records."""
    return TelemetryService.ingest_traces(session=session, items=payload.items)


# -----------------------------------------------------------------------------
# Query Endpoints
# -----------------------------------------------------------------------------


@router.get(
    "/metrics",
    response_model=MetricListResponse,
    status_code=status.HTTP_200_OK,
    summary="Query Time-Bounded Metric Observations",
    description="Retrieve metric observations filtered by service, metric name, environment, and time window.",
)
def query_metrics(
    service_name: str | None = Query(default=None, description="Filter by service name"),
    metric_name: str | None = Query(default=None, description="Filter by metric name"),
    environment: str | None = Query(default=None, description="Filter by environment"),
    start_time: datetime | None = Query(default=None, description="Start UTC timestamp"),
    end_time: datetime | None = Query(default=None, description="End UTC timestamp"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit (max 1000)"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> MetricListResponse:
    """Query persisted metric observations with strict pagination bounds."""
    models, total = TelemetryService.query_metrics(
        session=session,
        service_name=service_name,
        metric_name=metric_name,
        environment=environment,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )
    items = [
        MetricPoint(
            timestamp=m.timestamp,
            service_name=m.service_name,
            environment=m.environment,
            metric_name=m.metric_name,
            value=m.value,
            unit=m.unit,
            attributes=m.attributes or {},
            trace_id=m.trace_id,
            ingested_at=m.ingested_at,
        )
        for m in models
    ]
    return MetricListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/logs",
    response_model=LogListResponse,
    status_code=status.HTTP_200_OK,
    summary="Query Time-Bounded Structured Logs",
    description="Retrieve structured log records filtered by service, severity, trace ID, and time window.",
)
def query_logs(
    service_name: str | None = Query(default=None, description="Filter by service name"),
    severity: str | None = Query(
        default=None, description="Filter by severity (INFO, ERROR, etc.)"
    ),
    environment: str | None = Query(default=None, description="Filter by environment"),
    trace_id: str | None = Query(default=None, description="Filter by 32-hex trace ID"),
    start_time: datetime | None = Query(default=None, description="Start UTC timestamp"),
    end_time: datetime | None = Query(default=None, description="End UTC timestamp"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit (max 1000)"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> LogListResponse:
    """Query persisted structured logs with strict pagination bounds."""
    models, total = TelemetryService.query_logs(
        session=session,
        service_name=service_name,
        severity=severity,
        environment=environment,
        trace_id=trace_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )
    items = [
        LogRecordSchema(
            timestamp=m.timestamp,
            service_name=m.service_name,
            environment=m.environment,
            severity=m.severity,  # type: ignore[arg-type]
            message=m.message,
            attributes=m.attributes or {},
            trace_id=m.trace_id,
            span_id=m.span_id,
            exception_type=m.exception_type,
            error_class=m.error_class,
            ingested_at=m.ingested_at,
        )
        for m in models
    ]
    return LogListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/traces",
    response_model=TraceListResponse,
    status_code=status.HTTP_200_OK,
    summary="Query Trace Spans",
    description="Retrieve trace spans filtered by service, trace ID, min duration, and status code.",
)
def query_traces(
    service_name: str | None = Query(default=None, description="Filter by service name"),
    trace_id: str | None = Query(default=None, description="Filter by 32-hex trace ID"),
    min_duration_ms: float | None = Query(default=None, ge=0.0, description="Min duration in ms"),
    status_code: str | None = Query(
        default=None, description="Filter by status (OK, ERROR, UNSET)"
    ),
    start_time: datetime | None = Query(default=None, description="Start UTC timestamp"),
    end_time: datetime | None = Query(default=None, description="End UTC timestamp"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit (max 1000)"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> TraceListResponse:
    """Query persisted distributed trace spans."""
    models, total = TelemetryService.query_traces(
        session=session,
        service_name=service_name,
        trace_id=trace_id,
        min_duration_ms=min_duration_ms,
        status_code=status_code,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )
    items = [
        SpanRecordSchema(
            trace_id=m.trace_id,
            span_id=m.span_id,
            parent_span_id=m.parent_span_id,
            service_name=m.service_name,
            operation_name=m.operation_name,
            start_time=m.start_time,
            end_time=m.end_time,
            duration_ms=m.duration_ms,
            status_code=m.status_code,  # type: ignore[arg-type]
            status_message=m.status_message,
            attributes=m.attributes or {},
            ingested_at=m.ingested_at,
        )
        for m in models
    ]
    return TraceListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/traces/{trace_id}",
    response_model=TraceWaterfallResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Hierarchical Trace Waterfall",
    description="Retrieve all spans for a distributed trace ordered by parent-child hierarchy and time.",
)
def get_trace_waterfall(
    trace_id: str,
    session: Session = Depends(get_db),
) -> TraceWaterfallResponse:
    """Retrieve full trace span waterfall."""
    spans = TelemetryService.get_trace_waterfall(session=session, trace_id=trace_id)
    if not spans:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace with ID '{trace_id}' was not found.",
        )
    schema_spans = [
        SpanRecordSchema(
            trace_id=s.trace_id,
            span_id=s.span_id,
            parent_span_id=s.parent_span_id,
            service_name=s.service_name,
            operation_name=s.operation_name,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_ms=s.duration_ms,
            status_code=s.status_code,  # type: ignore[arg-type]
            status_message=s.status_message,
            attributes=s.attributes or {},
            ingested_at=s.ingested_at,
        )
        for s in spans
    ]
    return TraceWaterfallResponse(
        trace_id=trace_id,
        span_count=len(schema_spans),
        spans=schema_spans,
    )


# -----------------------------------------------------------------------------
# Observability Metrics Endpoint
# -----------------------------------------------------------------------------


@router.get(
    "/pipeline-metrics",
    response_model=PipelineMetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingestion Pipeline Operational Metrics",
    description="Returns low-cardinality counters and latency histograms for the telemetry pipeline.",
)
def get_pipeline_metrics() -> PipelineMetricsResponse:
    """Expose ingestion pipeline operational health metrics."""
    snapshot = get_metrics_snapshot()
    return PipelineMetricsResponse(
        counters=snapshot["counters"],
        durations_ms=snapshot["durations_ms"],
        active_experiments=snapshot["active_experiments"],
    )
