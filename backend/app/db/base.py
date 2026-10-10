"""Declarative base and foundational system models."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base class for all AegisOps ORM models."""

    pass


class SystemAuditLog(Base):
    """Immutable system audit ledger for recording system lifecycle and security events."""

    __tablename__ = "system_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<SystemAuditLog(id={self.id}, event_type='{self.event_type}', action='{self.action}', success={self.success})>"


# Import models here to ensure they register on Base.metadata for migrations
from app.db.models.experiment import ExperimentModel, FaultLabelModel  # noqa: E402
from app.db.models.investigation import (  # noqa: E402
    InvestigationEventModel,
    InvestigationModel,
    RemediationProposalModel,
)
from app.db.models.ml import AnomalyDetectionModel, MLModelModel  # noqa: E402
from app.db.models.telemetry import (  # noqa: E402
    LogRecordModel,
    MetricRecordModel,
    ServiceModel,
    TraceSpanModel,
)
from app.db.models.topology import (  # noqa: E402
    IncidentAnalysisModel,
    TopologyEdgeModel,
    TopologySnapshotModel,
)

__all__ = [
    "Base",
    "SystemAuditLog",
    "ServiceModel",
    "MetricRecordModel",
    "LogRecordModel",
    "TraceSpanModel",
    "ExperimentModel",
    "FaultLabelModel",
    "MLModelModel",
    "AnomalyDetectionModel",
    "TopologySnapshotModel",
    "TopologyEdgeModel",
    "IncidentAnalysisModel",
    "InvestigationModel",
    "InvestigationEventModel",
    "RemediationProposalModel",
]
