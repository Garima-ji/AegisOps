"""Typed state definition for LangGraph incident investigation workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    """Explicit typed state schema passed across LangGraph nodes."""

    # Investigation & Incident Identity
    investigation_id: str
    incident_id: str
    time_window_start: datetime
    time_window_end: datetime

    # Graph Execution Control & Budget
    current_step: str
    iteration: int
    tool_call_count: int
    budget_exhausted: bool

    # Gathered Evidence & Telemetry Bank
    evidence_records: list[dict[str, Any]]
    anomaly_events: list[dict[str, Any]]
    topology_summary: dict[str, Any]

    # Root Cause Candidates (Original Phase 4 Preservation)
    phase4_candidates: list[dict[str, Any]]
    leading_candidate: str | None
    candidate_assessments: dict[str, str]

    # Citations & Gap Analysis
    supporting_citations: list[str]
    contradicting_citations: list[str]
    missing_evidence: list[str]
    coverage_limitations: list[str]

    # Runbook References
    runbook_references: list[dict[str, Any]]

    # Remediation Proposal & Policy Gating
    proposed_remediation: dict[str, Any] | None
    policy_evaluation: dict[str, Any] | None
    simulation_result: dict[str, Any] | None
    approval_status: str  # "NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED"

    # Abstention
    is_abstaining: bool
    abstention_reason: str | None

    # Final Output & Auditing
    final_report: dict[str, Any] | None
    audit_events: list[dict[str, Any]]
    errors: list[str]

    # Context / Injected Dependencies
    db_session: Any
    llm_provider: Any
    require_approval_by_default: bool
