"""Bounded, typed tools for retrieving anomaly events, metrics, logs, and distributed trace spans."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.evidence import EvidenceRecord, EvidenceSourceType
from app.agent.tools.base import (
    MAX_LOG_QUERY_LEN,
    MAX_TELEMETRY_ITEMS,
    MAX_WINDOW_HOURS,
    BaseInvestigationTool,
)
from app.db.models.ml import AnomalyDetectionModel
from app.db.models.telemetry import LogRecordModel, MetricRecordModel, TraceSpanModel
from app.topology.models import _to_utc


class AnomalyQueryInput(BaseModel):
    """Input parameters for querying anomaly detections."""

    start_time: datetime
    end_time: datetime
    service_name: str | None = None
    limit: int = Field(default=30, ge=1, le=MAX_TELEMETRY_ITEMS)

    @field_validator("end_time")
    @classmethod
    def validate_window(cls, v: datetime, info: Any) -> datetime:
        st = info.data.get("start_time")
        if st and v <= st:
            raise ValueError(f"end_time ({v}) must be greater than start_time ({st})")
        if st and (v - st) > timedelta(hours=MAX_WINDOW_HOURS):
            raise ValueError(
                f"Query window exceeds maximum permitted span of {MAX_WINDOW_HOURS} hours."
            )
        return v


class GetAnomalyEventsTool(BaseInvestigationTool):
    """Tool retrieving bounded anomaly detections from the database."""

    name = "get_anomaly_events"
    description = (
        "Retrieve anomaly detection events for a bounded time window and optional service."
    )

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        db: Session = kwargs["db"]
        inp = AnomalyQueryInput(
            start_time=kwargs["start_time"],
            end_time=kwargs["end_time"],
            service_name=kwargs.get("service_name"),
            limit=kwargs.get("limit", 30),
        )

        st = _to_utc(inp.start_time)
        et = _to_utc(inp.end_time)

        stmt = select(AnomalyDetectionModel).where(
            AnomalyDetectionModel.timestamp >= st,
            AnomalyDetectionModel.timestamp <= et,
            AnomalyDetectionModel.is_anomaly.is_(True),
        )
        if inp.service_name:
            stmt = stmt.where(AnomalyDetectionModel.service_name == inp.service_name)

        stmt = stmt.order_by(desc(AnomalyDetectionModel.anomaly_score)).limit(inp.limit)
        records = list(db.execute(stmt).scalars().all())

        evidence_items: list[dict[str, Any]] = []
        for r in records:
            ev = EvidenceRecord.create(
                source_type=EvidenceSourceType.ANOMALY,
                service_name=r.service_name,
                timestamp=r.timestamp,
                summary=f"Anomaly detected on {r.service_name} with score {r.anomaly_score:.3f} by {r.detector_name}: {r.explanation or 'Triggered baseline threshold'}",
                raw_payload={
                    "detector": r.detector_name,
                    "score": r.anomaly_score,
                    "severity": r.severity,
                    "triggering_features": r.triggering_features or [],
                },
            )
            evidence_items.append(ev.model_dump())

        return {"anomalies": evidence_items, "count": len(evidence_items)}, len(evidence_items)


class MetricQueryInput(BaseModel):
    """Input parameters for querying service metrics."""

    service_name: str
    metric_names: list[str] | None = None
    start_time: datetime
    end_time: datetime
    limit: int = Field(default=50, ge=1, le=MAX_TELEMETRY_ITEMS)

    @field_validator("end_time")
    @classmethod
    def validate_window(cls, v: datetime, info: Any) -> datetime:
        st = info.data.get("start_time")
        if st and v <= st:
            raise ValueError(f"end_time ({v}) must be greater than start_time ({st})")
        if st and (v - st) > timedelta(hours=MAX_WINDOW_HOURS):
            raise ValueError(
                f"Query window exceeds maximum allowed window of {MAX_WINDOW_HOURS} hours."
            )
        return v


class QueryServiceMetricsTool(BaseInvestigationTool):
    """Tool querying bounded time-series metrics with statistical summaries."""

    name = "query_service_metrics"
    description = "Query service metrics and aggregated statistics across a bounded time window."

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        db: Session = kwargs["db"]
        inp = MetricQueryInput(
            service_name=kwargs["service_name"],
            metric_names=kwargs.get("metric_names"),
            start_time=kwargs["start_time"],
            end_time=kwargs["end_time"],
            limit=kwargs.get("limit", 50),
        )

        st = _to_utc(inp.start_time)
        et = _to_utc(inp.end_time)

        stmt = select(MetricRecordModel).where(
            MetricRecordModel.service_name == inp.service_name,
            MetricRecordModel.timestamp >= st,
            MetricRecordModel.timestamp <= et,
        )
        if inp.metric_names:
            stmt = stmt.where(MetricRecordModel.metric_name.in_(inp.metric_names))

        stmt = stmt.order_by(desc(MetricRecordModel.timestamp)).limit(inp.limit)
        records = list(db.execute(stmt).scalars().all())

        # Aggregate metrics
        metric_stats: dict[str, dict[str, Any]] = {}
        for r in records:
            m_name = r.metric_name
            if m_name not in metric_stats:
                metric_stats[m_name] = {
                    "count": 0,
                    "min": float("inf"),
                    "max": float("-inf"),
                    "sum": 0.0,
                    "unit": r.unit,
                    "latest_value": r.value,
                    "latest_timestamp": r.timestamp.isoformat(),
                }
            st_dict = metric_stats[m_name]
            st_dict["count"] += 1
            st_dict["min"] = min(st_dict["min"], r.value)
            st_dict["max"] = max(st_dict["max"], r.value)
            st_dict["sum"] += r.value

        for st_dict in metric_stats.values():
            if st_dict["count"] > 0:
                st_dict["avg"] = round(st_dict["sum"] / st_dict["count"], 3)
            del st_dict["sum"]

        # Formulate evidence records
        evidence_items: list[dict[str, Any]] = []
        for m_name, stats in metric_stats.items():
            ev = EvidenceRecord.create(
                source_type=EvidenceSourceType.METRIC,
                service_name=inp.service_name,
                timestamp=datetime.fromisoformat(stats["latest_timestamp"]),
                summary=f"Metric '{m_name}' on {inp.service_name}: latest={stats['latest_value']}, max={stats['max']}, avg={stats.get('avg', stats['latest_value'])} {stats['unit']}",
                raw_payload={"metric_name": m_name, "stats": stats},
            )
            evidence_items.append(ev.model_dump())

        return {
            "service_name": inp.service_name,
            "metrics": metric_stats,
            "evidence": evidence_items,
        }, len(records)


class LogQueryInput(BaseModel):
    """Input parameters for querying structured service logs."""

    service_name: str
    severity: str | None = None
    query_substring: str | None = Field(default=None, max_length=MAX_LOG_QUERY_LEN)
    start_time: datetime
    end_time: datetime
    limit: int = Field(default=40, ge=1, le=MAX_TELEMETRY_ITEMS)


class QueryServiceLogsTool(BaseInvestigationTool):
    """Tool querying bounded structured logs for errors and diagnostic patterns."""

    name = "query_service_logs"
    description = "Query structured log events for a service within a bounded window."

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        db: Session = kwargs["db"]
        inp = LogQueryInput(
            service_name=kwargs["service_name"],
            severity=kwargs.get("severity"),
            query_substring=kwargs.get("query_substring"),
            start_time=kwargs["start_time"],
            end_time=kwargs["end_time"],
            limit=kwargs.get("limit", 40),
        )

        st = _to_utc(inp.start_time)
        et = _to_utc(inp.end_time)

        stmt = select(LogRecordModel).where(
            LogRecordModel.service_name == inp.service_name,
            LogRecordModel.timestamp >= st,
            LogRecordModel.timestamp <= et,
        )
        if inp.severity:
            stmt = stmt.where(LogRecordModel.severity == inp.severity.upper())
        if inp.query_substring:
            stmt = stmt.where(LogRecordModel.message.ilike(f"%{inp.query_substring}%"))

        stmt = stmt.order_by(desc(LogRecordModel.timestamp)).limit(inp.limit)
        records = list(db.execute(stmt).scalars().all())

        logs_data: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []

        for r in records:
            # Sanitize log message to avoid raw markdown injection
            safe_msg = r.message.replace("\x00", "")[:200]
            logs_data.append(
                {
                    "timestamp": r.timestamp.isoformat(),
                    "severity": r.severity,
                    "message": safe_msg,
                    "exception_type": r.exception_type,
                    "trace_id": r.trace_id,
                }
            )
            if r.severity in ("ERROR", "CRITICAL", "WARNING"):
                ev = EvidenceRecord.create(
                    source_type=EvidenceSourceType.LOG,
                    service_name=inp.service_name,
                    timestamp=r.timestamp,
                    summary=f"Log [{r.severity}] on {inp.service_name}: {safe_msg}"
                    + (f" (Exception: {r.exception_type})" if r.exception_type else ""),
                    raw_payload={
                        "severity": r.severity,
                        "exception": r.exception_type,
                        "trace_id": r.trace_id,
                    },
                )
                evidence_items.append(ev.model_dump())

        return {
            "service_name": inp.service_name,
            "logs": logs_data,
            "evidence": evidence_items,
        }, len(records)


class TraceQueryInput(BaseModel):
    """Input parameters for querying distributed trace spans."""

    service_name: str | None = None
    trace_id: str | None = None
    status_code: str | None = None
    start_time: datetime
    end_time: datetime
    limit: int = Field(default=40, ge=1, le=MAX_TELEMETRY_ITEMS)


class QueryTraceSpansTool(BaseInvestigationTool):
    """Tool querying distributed trace spans to inspect latency propagation and call chains."""

    name = "query_trace_spans"
    description = "Query trace spans and parent-child dependencies across a bounded time window."

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        db: Session = kwargs["db"]
        inp = TraceQueryInput(
            service_name=kwargs.get("service_name"),
            trace_id=kwargs.get("trace_id"),
            status_code=kwargs.get("status_code"),
            start_time=kwargs["start_time"],
            end_time=kwargs["end_time"],
            limit=kwargs.get("limit", 40),
        )

        st = _to_utc(inp.start_time)
        et = _to_utc(inp.end_time)

        stmt = select(TraceSpanModel).where(
            TraceSpanModel.start_time >= st,
            TraceSpanModel.start_time <= et,
        )
        if inp.service_name:
            stmt = stmt.where(TraceSpanModel.service_name == inp.service_name)
        if inp.trace_id:
            stmt = stmt.where(TraceSpanModel.trace_id == inp.trace_id)
        if inp.status_code:
            stmt = stmt.where(TraceSpanModel.status_code == inp.status_code.upper())

        stmt = stmt.order_by(desc(TraceSpanModel.duration_ms)).limit(inp.limit)
        records = list(db.execute(stmt).scalars().all())

        spans_data: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []

        for s in records:
            span_entry = {
                "trace_id": s.trace_id,
                "span_id": s.span_id,
                "parent_span_id": s.parent_span_id,
                "service_name": s.service_name,
                "operation_name": s.operation_name,
                "duration_ms": s.duration_ms,
                "status_code": s.status_code,
                "start_time": s.start_time.isoformat(),
            }
            spans_data.append(span_entry)

            if s.status_code == "ERROR" or s.duration_ms > 500.0:
                ev = EvidenceRecord.create(
                    source_type=EvidenceSourceType.TRACE,
                    service_name=s.service_name,
                    timestamp=s.start_time,
                    summary=f"Trace span on {s.service_name} operation '{s.operation_name}' status={s.status_code} duration={s.duration_ms:.1f}ms",
                    raw_payload={
                        "trace_id": s.trace_id,
                        "span_id": s.span_id,
                        "duration_ms": s.duration_ms,
                        "status_code": s.status_code,
                    },
                )
                evidence_items.append(ev.model_dump())

        return {
            "spans": spans_data,
            "evidence": evidence_items,
            "total_count": len(records),
        }, len(records)
