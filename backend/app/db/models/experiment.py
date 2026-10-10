"""SQLAlchemy models for fault injection experiments and ground-truth labels."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExperimentModel(Base):
    """Fault injection experiments ledger."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    target_service: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fault_type: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    initiating_service: Mapped[str] = mapped_column(String(64), nullable=False)
    symptom_services: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    cleanup_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Experiment(id='{self.id}', scenario='{self.scenario_name}', status='{self.status}')>"
        )


class FaultLabelModel(Base):
    """Ground-truth labels for ML anomaly detection and root-cause evaluation."""

    __tablename__ = "fault_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_fault: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fault_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="HIGH")
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_labels_exp_svc_time", "experiment_id", "service_name", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<FaultLabel(exp='{self.experiment_id}', svc='{self.service_name}', fault={self.is_fault})>"
