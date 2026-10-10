"""Structured, grounded investigation report schemas and builders."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agent.evidence import CitationValidationResult


class RunbookReference(BaseModel):
    """Runbook citation with excerpt and matched sections."""

    runbook_id: str
    title: str
    target_service: str
    relevant_section: str
    excerpt: str
    citation_id: str


class CandidateEvaluation(BaseModel):
    """Detailed evidence assessment for a root-cause candidate."""

    service_name: str
    phase4_rank: int
    phase4_score: float
    status: str = Field(description="'SUPPORTED', 'CONTRADICTED', or 'INCONCLUSIVE'")
    supporting_citation_ids: list[str] = Field(default_factory=list)
    contradicting_citation_ids: list[str] = Field(default_factory=list)
    reasoning: str


class ProposedRemediationPlan(BaseModel):
    """Action proposed by the agent or recommended from runbook."""

    action_type: str
    target_service: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    supporting_citation_ids: list[str] = Field(default_factory=list)
    potential_risk: str
    reversibility: str
    policy_decision: str = Field(description="'ALLOW', 'DENY', or 'REQUIRES_APPROVAL'")
    approval_status: str = Field(description="'PENDING', 'APPROVED', 'REJECTED', or 'NOT_REQUIRED'")
    dry_run_result: dict[str, Any] | None = None
    simulation_status: str = Field(
        default="NOT_EXECUTED",
        description="'NOT_EXECUTED', 'DRY_RUN', 'SIMULATED_SUCCESS', or 'SIMULATED_FAILED'",
    )


class InvestigationReport(BaseModel):
    """Validated, evidence-grounded final incident investigation report."""

    version: str = Field(default="1.0.0", description="Report schema version")
    investigation_id: str
    incident_id: str
    time_window_start: datetime
    time_window_end: datetime
    summary: str
    severity: str
    confidence_classification: str = Field(
        description="'HIGH', 'MEDIUM', 'LOW', or 'INSUFFICIENT_EVIDENCE'"
    )
    confidence_score: float = Field(ge=0.0, le=1.0)

    # Ground truth / Phase 4 candidate preservation
    phase4_root_cause_ranking: list[dict[str, Any]] = Field(
        default_factory=list, description="Original un-modified Phase 4 candidates"
    )
    leading_candidate: str | None = None
    candidate_evaluations: list[CandidateEvaluation] = Field(default_factory=list)

    # Grounded Citations
    supporting_evidence_citations: list[str] = Field(default_factory=list)
    contradicting_evidence_citations: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    coverage_limitations: list[str] = Field(default_factory=list)

    # Runbook & Diagnostic Advice
    runbook_references: list[RunbookReference] = Field(default_factory=list)
    recommended_diagnostic_step: str

    # Remediation
    proposed_remediation: ProposedRemediationPlan | None = None

    # Abstention
    is_abstained: bool = False
    abstention_reason: str | None = None

    # Audit & Tool summary
    investigation_duration_seconds: float = 0.0
    tool_call_count: int = 0
    citation_validation: CitationValidationResult | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def validate_all_citations(self, evidence_bank: Any) -> bool:
        """Validate all citations contained across report sections."""
        from app.agent.evidence import validate_citations

        all_claimed: list[str] = []
        all_claimed.extend(self.supporting_evidence_citations)
        all_claimed.extend(self.contradicting_evidence_citations)
        for cand in self.candidate_evaluations:
            all_claimed.extend(cand.supporting_citation_ids)
            all_claimed.extend(cand.contradicting_citation_ids)
        for rb in self.runbook_references:
            if rb.citation_id:
                all_claimed.append(rb.citation_id)
        if self.proposed_remediation:
            all_claimed.extend(self.proposed_remediation.supporting_citation_ids)

        # Unique ordered list
        unique_claimed = list(dict.fromkeys(all_claimed))
        val_result = validate_citations(unique_claimed, evidence_bank)
        self.citation_validation = val_result
        return val_result.is_valid
