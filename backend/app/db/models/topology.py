"""SQLAlchemy models for service dependency topology and incident root-cause analysis."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TopologySnapshotModel(Base):
    """Versioned service dependency topology snapshots built from observed traces."""

    __tablename__ = "topology_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0.0")
    data_source: Mapped[str] = mapped_column(String(64), nullable=False, default="trace_spans")
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    services_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    def __repr__(self) -> str:
        return f"<TopologySnapshot(id='{self.id}', version='{self.version}', services={self.service_count}, edges={self.edge_count})>"


class TopologyEdgeModel(Base):
    """Observed directed cross-service dependency edges (caller -> downstream)."""

    __tablename__ = "topology_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topology_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("topology_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    caller_service: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    downstream_service: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_observed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ix_edges_topo_caller_downstream",
            "topology_id",
            "caller_service",
            "downstream_service",
        ),
    )

    def __repr__(self) -> str:
        return f"<TopologyEdge({self.caller_service} -> {self.downstream_service} calls={self.call_count})>"


class IncidentAnalysisModel(Base):
    """Persisted incident root-cause analysis runs and ranked candidates."""

    __tablename__ = "incident_analyses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="COMPLETED"
    )  # COMPLETED, INSUFFICIENT_EVIDENCE, FAILED
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    topology_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    affected_services: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    top_candidate_service: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    diagnostics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    def __repr__(self) -> str:
        return f"<IncidentAnalysis(id='{self.id}', top='{self.top_candidate_service}', status='{self.status}')>"
