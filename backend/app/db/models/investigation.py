"""SQLAlchemy models for agentic investigations, audit events, and remediation proposals."""

from __future__ import annotations

import uuid
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


class InvestigationModel(Base):
    """Persisted incident investigation runs, findings, and grounded reports."""

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="IN_PROGRESS"
    )  # IN_PROGRESS, COMPLETED, ABSTAINED, FAILED
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    leading_candidate: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_abstained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    abstention_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("ix_investigations_status_created", "status", "created_at"),)

    def __repr__(self) -> str:
        return f"<Investigation(id='{self.id}', incident='{self.incident_id}', status='{self.status}')>"


class InvestigationEventModel(Base):
    """Immutable audit trail of state graph transitions and tool invocations."""

    __tablename__ = "investigation_events"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"evt-{uuid.uuid4().hex[:12]}"
    )
    investigation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("ix_inv_events_inv_time", "investigation_id", "timestamp"),)

    def __repr__(self) -> str:
        return f"<InvestigationEvent({self.investigation_id} type='{self.event_type}')>"


class RemediationProposalModel(Base):
    """Persisted remediation actions, policy checks, approvals, and simulation outcomes."""

    __tablename__ = "remediation_proposals"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"rem-{uuid.uuid4().hex[:12]}"
    )
    investigation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_service: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    policy_decision: Mapped[str] = mapped_column(
        String(32), nullable=False, default="DENY"
    )  # ALLOW, DENY, REQUIRES_APPROVAL
    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NOT_REQUIRED"
    )  # NOT_REQUIRED, PENDING, APPROVED, REJECTED
    simulation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NOT_EXECUTED"
    )  # NOT_EXECUTED, DRY_RUN, SIMULATED_SUCCESS, SIMULATED_FAILED
    simulation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RemediationProposal({self.action_type} for {self.target_service} policy='{self.policy_decision}')>"
