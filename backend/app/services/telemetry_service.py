"""Core domain service for telemetry ingestion, validation, normalization, and querying."""

import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.db.models.telemetry import LogRecordModel, MetricRecordModel, TraceSpanModel
from app.observability.metrics import increment_counter, record_duration
from app.schemas.telemetry import (
    IngestionItemError,
    IngestionResponse,
    LogRecordSchema,
    MetricPoint,
    SpanRecordSchema,
)
from app.services.registry_service import upsert_service

MAX_QUERY_LIMIT = 1000
DEFAULT_QUERY_LIMIT = 100
MAX_QUERY_WINDOW_DAYS = 30
FUTURE_TOLERANCE_MINUTES = 5


def validate_query_window(start_time: datetime | None, end_time: datetime | None) -> None:
    """Validate that query time bounds are logically ordered and bounded."""
    if start_time and end_time:
        if end_time < start_time:
            raise ValidationError("Query 'end_time' cannot precede 'start_time'.")
        if (end_time - start_time) > timedelta(days=MAX_QUERY_WINDOW_DAYS):
            raise ValidationError(f"Query time window cannot exceed {MAX_QUERY_WINDOW_DAYS} days.")


class TelemetryService:
    """Service orchestrating telemetry ingestion and safe querying."""

    @staticmethod
    def ingest_metrics(session: Session, items: list[MetricPoint]) -> IngestionResponse:
        """Validate, normalize, register service, and persist a batch of metrics."""
        start_t = time.perf_counter()
        now = datetime.now(UTC)
        max_future = now + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)

        accepted_records: list[MetricRecordModel] = []
        errors: list[IngestionItemError] = []

        # Deduplicate service upserts within this batch
        registered_services: set[tuple[str, str]] = set()

        for idx, item in enumerate(items):
            # Future timestamp guard
            if item.timestamp > max_future:
                errors.append(
                    IngestionItemError(
                        index=idx,
                        field="timestamp",
                        reason=f"Timestamp '{item.timestamp}' exceeds future tolerance limit.",
                    )
                )
                continue

            # Ensure service registration
            key = (item.service_name, item.environment)
            if key not in registered_services:
                upsert_service(
                    session=session,
                    service_name=item.service_name,
                    environment=item.environment,
                )
                registered_services.add(key)

            record = MetricRecordModel(
                timestamp=item.timestamp,
                service_name=item.service_name,
                environment=item.environment,
                metric_name=item.metric_name,
                value=item.value,
                unit=item.unit,
                attributes=item.attributes,
                trace_id=item.trace_id,
                ingested_at=now,
            )
            accepted_records.append(record)

        if accepted_records:
            session.add_all(accepted_records)
            session.flush()

        duration_ms = round((time.perf_counter() - start_t) * 1000, 2)

        # Update metrics
        increment_counter("telemetry.metrics.received_total", len(items))
        increment_counter("telemetry.metrics.accepted_total", len(accepted_records))
        increment_counter("telemetry.metrics.rejected_total", len(errors))
        increment_counter("telemetry.ingestion.batches_total", 1)
        record_duration("metrics", duration_ms)

        status_str: Literal["success", "partial_success", "failed"] = (
            "success" if not errors else ("failed" if not accepted_records else "partial_success")
        )
        return IngestionResponse(
            status=status_str,
            received=len(items),
            accepted=len(accepted_records),
            rejected=len(errors),
            errors=errors,
            duration_ms=duration_ms,
        )

    @staticmethod
    def ingest_logs(session: Session, items: list[LogRecordSchema]) -> IngestionResponse:
        """Validate, normalize, and persist structured log events."""
        start_t = time.perf_counter()
        now = datetime.now(UTC)
        max_future = now + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)

        accepted_records: list[LogRecordModel] = []
        errors: list[IngestionItemError] = []
        registered_services: set[tuple[str, str]] = set()

        for idx, item in enumerate(items):
            if item.timestamp > max_future:
                errors.append(
                    IngestionItemError(
                        index=idx,
                        field="timestamp",
                        reason=f"Log timestamp '{item.timestamp}' exceeds future tolerance.",
                    )
                )
                continue

            key = (item.service_name, item.environment)
            if key not in registered_services:
                upsert_service(
                    session=session,
                    service_name=item.service_name,
                    environment=item.environment,
                )
                registered_services.add(key)

            record = LogRecordModel(
                timestamp=item.timestamp,
                service_name=item.service_name,
                environment=item.environment,
                severity=item.severity,
                message=item.message,
                attributes=item.attributes,
                trace_id=item.trace_id,
                span_id=item.span_id,
                exception_type=item.exception_type,
                error_class=item.error_class,
                ingested_at=now,
            )
            accepted_records.append(record)

        if accepted_records:
            session.add_all(accepted_records)
            session.flush()

        duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
        increment_counter("telemetry.logs.received_total", len(items))
        increment_counter("telemetry.logs.accepted_total", len(accepted_records))
        increment_counter("telemetry.logs.rejected_total", len(errors))
        increment_counter("telemetry.ingestion.batches_total", 1)
        record_duration("logs", duration_ms)

        status_str: Literal["success", "partial_success", "failed"] = (
            "success" if not errors else ("failed" if not accepted_records else "partial_success")
        )
        return IngestionResponse(
            status=status_str,
            received=len(items),
            accepted=len(accepted_records),
            rejected=len(errors),
            errors=errors,
            duration_ms=duration_ms,
        )

    @staticmethod
    def ingest_traces(session: Session, items: list[SpanRecordSchema]) -> IngestionResponse:
        """Validate, normalize, and persist distributed trace span events."""
        start_t = time.perf_counter()
        now = datetime.now(UTC)
        max_future = now + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)

        accepted_records: list[TraceSpanModel] = []
        errors: list[IngestionItemError] = []
        registered_services: set[str] = set()
        seen_spans_in_batch: set[tuple[str, str]] = set()

        for idx, item in enumerate(items):
            if item.start_time > max_future:
                errors.append(
                    IngestionItemError(
                        index=idx,
                        field="start_time",
                        reason=f"Span start_time '{item.start_time}' exceeds future tolerance.",
                    )
                )
                continue

            span_key = (item.trace_id, item.span_id)
            if span_key in seen_spans_in_batch:
                errors.append(
                    IngestionItemError(
                        index=idx,
                        field="span_id",
                        reason=f"Duplicate span ('{item.trace_id}', '{item.span_id}') in same batch.",
                    )
                )
                continue
            seen_spans_in_batch.add(span_key)

            if item.service_name not in registered_services:
                upsert_service(
                    session=session,
                    service_name=item.service_name,
                    environment="production",
                )
                registered_services.add(item.service_name)

            dur_ms = (
                item.duration_ms
                if item.duration_ms is not None
                else (item.end_time - item.start_time).total_seconds() * 1000
            )

            record = TraceSpanModel(
                trace_id=item.trace_id,
                span_id=item.span_id,
                parent_span_id=item.parent_span_id,
                service_name=item.service_name,
                operation_name=item.operation_name,
                start_time=item.start_time,
                end_time=item.end_time,
                duration_ms=dur_ms,
                status_code=item.status_code,
                status_message=item.status_message,
                attributes=item.attributes,
                ingested_at=now,
            )
            accepted_records.append(record)

        if accepted_records:
            session.add_all(accepted_records)
            session.flush()

        duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
        increment_counter("telemetry.traces.received_total", len(items))
        increment_counter("telemetry.traces.accepted_total", len(accepted_records))
        increment_counter("telemetry.traces.rejected_total", len(errors))
        increment_counter("telemetry.ingestion.batches_total", 1)
        record_duration("traces", duration_ms)

        status_str: Literal["success", "partial_success", "failed"] = (
            "success" if not errors else ("failed" if not accepted_records else "partial_success")
        )
        return IngestionResponse(
            status=status_str,
            received=len(items),
            accepted=len(accepted_records),
            rejected=len(errors),
            errors=errors,
            duration_ms=duration_ms,
        )

    # -------------------------------------------------------------------------
    # Time-Bounded Query Methods
    # -------------------------------------------------------------------------

    @staticmethod
    def query_metrics(
        session: Session,
        service_name: str | None = None,
        metric_name: str | None = None,
        environment: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
        offset: int = 0,
    ) -> tuple[list[MetricRecordModel], int]:
        """Query time-bounded metric observations with deterministic ordering."""
        validate_query_window(start_time, end_time)
        bounded_limit = min(max(1, limit), MAX_QUERY_LIMIT)

        stmt = select(MetricRecordModel)
        count_stmt = select(func.count(MetricRecordModel.id))

        if service_name:
            stmt = stmt.where(MetricRecordModel.service_name == service_name)
            count_stmt = count_stmt.where(MetricRecordModel.service_name == service_name)
        if metric_name:
            stmt = stmt.where(MetricRecordModel.metric_name == metric_name)
            count_stmt = count_stmt.where(MetricRecordModel.metric_name == metric_name)
        if environment:
            stmt = stmt.where(MetricRecordModel.environment == environment)
            count_stmt = count_stmt.where(MetricRecordModel.environment == environment)
        if start_time:
            stmt = stmt.where(MetricRecordModel.timestamp >= start_time)
            count_stmt = count_stmt.where(MetricRecordModel.timestamp >= start_time)
        if end_time:
            stmt = stmt.where(MetricRecordModel.timestamp <= end_time)
            count_stmt = count_stmt.where(MetricRecordModel.timestamp <= end_time)

        total = session.execute(count_stmt).scalar_one() or 0
        stmt = stmt.order_by(MetricRecordModel.timestamp.desc()).limit(bounded_limit).offset(offset)
        items = list(session.execute(stmt).scalars().all())
        return items, total

    @staticmethod
    def query_logs(
        session: Session,
        service_name: str | None = None,
        severity: str | None = None,
        environment: str | None = None,
        trace_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
        offset: int = 0,
    ) -> tuple[list[LogRecordModel], int]:
        """Query time-bounded structured logs."""
        validate_query_window(start_time, end_time)
        bounded_limit = min(max(1, limit), MAX_QUERY_LIMIT)

        stmt = select(LogRecordModel)
        count_stmt = select(func.count(LogRecordModel.id))

        if service_name:
            stmt = stmt.where(LogRecordModel.service_name == service_name)
            count_stmt = count_stmt.where(LogRecordModel.service_name == service_name)
        if severity:
            stmt = stmt.where(LogRecordModel.severity == severity.upper())
            count_stmt = count_stmt.where(LogRecordModel.severity == severity.upper())
        if environment:
            stmt = stmt.where(LogRecordModel.environment == environment)
            count_stmt = count_stmt.where(LogRecordModel.environment == environment)
        if trace_id:
            stmt = stmt.where(LogRecordModel.trace_id == trace_id)
            count_stmt = count_stmt.where(LogRecordModel.trace_id == trace_id)
        if start_time:
            stmt = stmt.where(LogRecordModel.timestamp >= start_time)
            count_stmt = count_stmt.where(LogRecordModel.timestamp >= start_time)
        if end_time:
            stmt = stmt.where(LogRecordModel.timestamp <= end_time)
            count_stmt = count_stmt.where(LogRecordModel.timestamp <= end_time)

        total = session.execute(count_stmt).scalar_one() or 0
        stmt = stmt.order_by(LogRecordModel.timestamp.desc()).limit(bounded_limit).offset(offset)
        items = list(session.execute(stmt).scalars().all())
        return items, total

    @staticmethod
    def query_traces(
        session: Session,
        service_name: str | None = None,
        trace_id: str | None = None,
        min_duration_ms: float | None = None,
        status_code: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
        offset: int = 0,
    ) -> tuple[list[TraceSpanModel], int]:
        """Query trace span events with filtering."""
        validate_query_window(start_time, end_time)
        bounded_limit = min(max(1, limit), MAX_QUERY_LIMIT)

        stmt = select(TraceSpanModel)
        count_stmt = select(func.count(TraceSpanModel.id))

        if service_name:
            stmt = stmt.where(TraceSpanModel.service_name == service_name)
            count_stmt = count_stmt.where(TraceSpanModel.service_name == service_name)
        if trace_id:
            stmt = stmt.where(TraceSpanModel.trace_id == trace_id)
            count_stmt = count_stmt.where(TraceSpanModel.trace_id == trace_id)
        if min_duration_ms is not None:
            stmt = stmt.where(TraceSpanModel.duration_ms >= min_duration_ms)
            count_stmt = count_stmt.where(TraceSpanModel.duration_ms >= min_duration_ms)
        if status_code:
            stmt = stmt.where(TraceSpanModel.status_code == status_code)
            count_stmt = count_stmt.where(TraceSpanModel.status_code == status_code)
        if start_time:
            stmt = stmt.where(TraceSpanModel.start_time >= start_time)
            count_stmt = count_stmt.where(TraceSpanModel.start_time >= start_time)
        if end_time:
            stmt = stmt.where(TraceSpanModel.start_time <= end_time)
            count_stmt = count_stmt.where(TraceSpanModel.start_time <= end_time)

        total = session.execute(count_stmt).scalar_one() or 0
        stmt = stmt.order_by(TraceSpanModel.start_time.desc()).limit(bounded_limit).offset(offset)
        items = list(session.execute(stmt).scalars().all())
        return items, total

    @staticmethod
    def get_trace_waterfall(session: Session, trace_id: str) -> list[TraceSpanModel]:
        """Retrieve all spans for a trace_id ordered in hierarchical waterfall sequence."""
        stmt = (
            select(TraceSpanModel)
            .where(TraceSpanModel.trace_id == trace_id.strip().lower())
            .order_by(
                TraceSpanModel.parent_span_id.nulls_first(),
                TraceSpanModel.start_time.asc(),
            )
        )
        return list(session.execute(stmt).scalars().all())
