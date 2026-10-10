"""Typed remediation action catalog and outcome models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RemediationActionType(StrEnum):
    """Permitted safe simulated remediation actions."""

    STOP_FAULT_SCENARIO = "stop_fault_scenario"
    RESTORE_SERVICE = "restore_service"
    MITIGATE_LATENCY_INJECTION = "mitigate_latency_injection"
    RECOMMEND_RUNBOOK_STEP = "recommend_runbook_step"


class PolicyDecision(StrEnum):
    """Deterministic policy decision outcome."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


class ApprovalStatus(StrEnum):
    """State of human operator approval."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SimulationStatus(StrEnum):
    """Outcome of simulated execution against controlled simulator."""

    NOT_EXECUTED = "NOT_EXECUTED"
    DRY_RUN = "DRY_RUN"
    SIMULATED_SUCCESS = "SIMULATED_SUCCESS"
    SIMULATED_FAILED = "SIMULATED_FAILED"


class RemediationProposal(BaseModel):
    """Proposed remediation action with parameters and safety attributes."""

    action_type: RemediationActionType
    target_service: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    supporting_citation_ids: list[str] = Field(default_factory=list)
    potential_risk: str = "Low - bounded in-memory simulation"
    reversibility: str = "Reversible via simulator reset or fault reactivation"


class PolicyEvaluationResult(BaseModel):
    """Outcome of evaluating a remediation proposal against deterministic policy."""

    decision: PolicyDecision
    reason: str
    violations: list[str] = Field(default_factory=list)
    requires_approval: bool
    proposal: RemediationProposal


class RemediationExecutionResult(BaseModel):
    """Result of dry-run or simulated execution."""

    status: SimulationStatus
    message: str
    action_type: RemediationActionType
    target_service: str
    pre_execution_state: dict[str, Any] = Field(default_factory=dict)
    post_execution_state: dict[str, Any] = Field(default_factory=dict)
    state_verified: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
