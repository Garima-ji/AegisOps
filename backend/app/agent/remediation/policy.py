"""Deterministic remediation policy engine for safety gating and boundary enforcement."""

from __future__ import annotations

from app.agent.remediation.actions import (
    PolicyDecision,
    PolicyEvaluationResult,
    RemediationActionType,
    RemediationProposal,
)
from app.ingestion.fault_injection import SAFETY_ALLOWLISTED_SERVICES


class RemediationPolicyEngine:
    """Deterministic, code-level safety policy governing all proposed operational actions."""

    def __init__(self, require_approval_by_default: bool = False) -> None:
        self.require_approval_by_default = require_approval_by_default

    def evaluate(
        self,
        proposal: RemediationProposal,
        require_approval_override: bool | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate a proposal strictly using deterministic safety rules."""
        violations: list[str] = []

        # Rule 1: Target service allowlist
        if proposal.target_service not in SAFETY_ALLOWLISTED_SERVICES:
            violations.append(
                f"Target service '{proposal.target_service}' is prohibited. "
                f"Only explicitly allowlisted simulation services {sorted(SAFETY_ALLOWLISTED_SERVICES)} are permitted."
            )

        # Rule 2: Valid Action Type
        try:
            action_type = RemediationActionType(proposal.action_type)
        except ValueError:
            violations.append(
                f"Action type '{proposal.action_type}' is not recognized or permitted."
            )
            return PolicyEvaluationResult(
                decision=PolicyDecision.DENY,
                reason="Prohibited action type",
                violations=violations,
                requires_approval=False,
                proposal=proposal,
            )

        # Rule 3: Parameter Bounds & Types
        params = proposal.parameters or {}
        if action_type == RemediationActionType.STOP_FAULT_SCENARIO:
            exp_id = params.get("experiment_id")
            if not exp_id or not isinstance(exp_id, str):
                violations.append(
                    "Action 'stop_fault_scenario' requires non-empty string 'experiment_id' parameter."
                )
        elif action_type == RemediationActionType.MITIGATE_LATENCY_INJECTION:
            lat = params.get("new_latency_ms", 0.0)
            if not isinstance(lat, (int, float)) or lat < 0.0 or lat > 2000.0:
                violations.append(
                    "Action 'mitigate_latency_injection' requires 'new_latency_ms' in range [0.0, 2000.0]."
                )
        elif action_type == RemediationActionType.RECOMMEND_RUNBOOK_STEP:
            # Recommendation only - always safe as it executes nothing
            pass

        # Rule 4: Grounded Evidence Prerequisite
        if action_type != RemediationActionType.RECOMMEND_RUNBOOK_STEP:
            if not proposal.supporting_citation_ids:
                violations.append(
                    "Remediation execution requires at least one validated supporting evidence citation ID."
                )

        # If any hard safety rule was violated -> DENY
        if violations:
            return PolicyEvaluationResult(
                decision=PolicyDecision.DENY,
                reason="Deterministic safety policy violation",
                violations=violations,
                requires_approval=False,
                proposal=proposal,
            )

        # Rule 5: Approval Gating
        require_approval = (
            require_approval_override
            if require_approval_override is not None
            else self.require_approval_by_default
        )

        # High-impact service actions or stopping fault scenarios require approval unless explicit auto-pilot
        if action_type == RemediationActionType.STOP_FAULT_SCENARIO and require_approval:
            return PolicyEvaluationResult(
                decision=PolicyDecision.REQUIRES_APPROVAL,
                reason="Halting active fault scenario requires operator sign-off",
                violations=[],
                requires_approval=True,
                proposal=proposal,
            )

        if require_approval and action_type != RemediationActionType.RECOMMEND_RUNBOOK_STEP:
            return PolicyEvaluationResult(
                decision=PolicyDecision.REQUIRES_APPROVAL,
                reason="Action requires operator approval under current policy configuration",
                violations=[],
                requires_approval=True,
                proposal=proposal,
            )

        return PolicyEvaluationResult(
            decision=PolicyDecision.ALLOW,
            reason="Action conforms to all deterministic safety constraints",
            violations=[],
            requires_approval=False,
            proposal=proposal,
        )
