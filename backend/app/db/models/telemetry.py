"""SQLAlchemy 2.0 ORM models for telemetry and service registry."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceModel(Base):
    """Registered microservice registry table."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, default="production")
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="healthy")
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (UniqueConstraint("name", "environment", name="uq_service_name_env"),)

    def __repr__(self) -> str:
        return f"<Service(name='{self.name}', env='{self.environment}', status='{self.status}')>"


class MetricRecordModel(Base):
    """Time-series metric observations table."""

    __tablename__ = "metric_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, default="production")
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="count")
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_metrics_svc_name_time", "service_name", "metric_name", "timestamp"),
        Index("ix_metrics_env_time", "environment", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<MetricRecord({self.service_name}:{self.metric_name}={self.value} {self.unit})>"


class LogRecordModel(Base):
    """Structured log events table."""

    __tablename__ = "log_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, default="production")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exception_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_logs_svc_sev_time", "service_name", "severity", "timestamp"),
        Index("ix_logs_trace_time", "trace_id", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<LogRecord({self.service_name} [{self.severity}]: {self.message[:30]})>"


class TraceSpanModel(Base):
    """OpenTelemetry-compatible distributed trace span records table."""

    __tablename__ = "trace_spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    service_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation_name: Mapped[str] = mapped_column(String(128), nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    status_code: Mapped[str] = mapped_column(String(16), nullable=False, default="UNSET")
    status_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_spans_trace_start", "trace_id", "start_time"),
        Index("ix_spans_svc_start", "service_name", "start_time"),
        UniqueConstraint("trace_id", "span_id", name="uq_trace_id_span_id"),
    )

    def __repr__(self) -> str:
        return f"<TraceSpan({self.service_name}:{self.operation_name} dur={self.duration_ms}ms)>"
