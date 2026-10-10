"""Bounded tools for evaluating remediation safety policy and executing safe simulations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent.remediation.actions import (
    PolicyDecision,
    PolicyEvaluationResult,
    RemediationActionType,
    RemediationProposal,
)
from app.agent.remediation.policy import RemediationPolicyEngine
from app.agent.remediation.simulator import RemediationSimulationExecutor
from app.agent.tools.base import BaseInvestigationTool


class RemediationPolicyInput(BaseModel):
    """Input parameters for evaluating a proposed remediation action."""

    action_type: str
    target_service: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = "Automated remediation investigation proposal"
    supporting_citation_ids: list[str] = Field(default_factory=list)


class EvaluateRemediationPolicyTool(BaseInvestigationTool):
    """Tool evaluating a proposed remediation action against deterministic safety policy."""

    name = "evaluate_remediation_policy"
    description = "Evaluate proposed remediation against deterministic policy rules (allowlists, bounds, approvals)."

    def __init__(self, policy_engine: RemediationPolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or RemediationPolicyEngine()

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        inp = RemediationPolicyInput(
            action_type=kwargs["action_type"],
            target_service=kwargs["target_service"],
            parameters=kwargs.get("parameters", {}),
            rationale=kwargs.get("rationale", "Proposed remediation"),
            supporting_citation_ids=kwargs.get("supporting_citation_ids", []),
        )

        try:
            action_type_enum = RemediationActionType(inp.action_type)
        except ValueError:
            return {
                "decision": PolicyDecision.DENY.value,
                "reason": f"Unknown action type '{inp.action_type}'",
                "violations": [f"Action type '{inp.action_type}' is unsupported."],
                "requires_approval": False,
            }, 0

        proposal = RemediationProposal(
            action_type=action_type_enum,
            target_service=inp.target_service,
            parameters=inp.parameters,
            rationale=inp.rationale,
            supporting_citation_ids=inp.supporting_citation_ids,
        )

        result: PolicyEvaluationResult = self.policy_engine.evaluate(proposal)
        return {
            "decision": result.decision.value,
            "reason": result.reason,
            "violations": result.violations,
            "requires_approval": result.requires_approval,
            "proposal": proposal.model_dump(),
        }, 1


class ExecuteSimulatedRemediationInput(BaseModel):
    """Input parameters for simulating a remediation action."""

    action_type: str
    target_service: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    is_approved: bool = False
    supporting_citation_ids: list[str] = Field(default_factory=list)


class ExecuteSimulatedRemediationTool(BaseInvestigationTool):
    """Tool executing permitted remediation actions strictly inside the controlled simulator."""

    name = "execute_simulated_remediation"
    description = "Execute an approved remediation action within the in-process controlled fault simulation environment."

    def __init__(
        self,
        policy_engine: RemediationPolicyEngine | None = None,
        simulator: RemediationSimulationExecutor | None = None,
    ) -> None:
        self.policy_engine = policy_engine or RemediationPolicyEngine()
        self.simulator = simulator or RemediationSimulationExecutor()

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        inp = ExecuteSimulatedRemediationInput(
            action_type=kwargs["action_type"],
            target_service=kwargs["target_service"],
            parameters=kwargs.get("parameters", {}),
            dry_run=kwargs.get("dry_run", False),
            is_approved=kwargs.get("is_approved", False),
            supporting_citation_ids=kwargs.get("supporting_citation_ids", []),
        )

        action_type_enum = RemediationActionType(inp.action_type)
        proposal = RemediationProposal(
            action_type=action_type_enum,
            target_service=inp.target_service,
            parameters=inp.parameters,
            rationale="Simulated remediation execution",
            supporting_citation_ids=inp.supporting_citation_ids,
        )

        if inp.dry_run:
            exec_res = self.simulator.dry_run(proposal)
            return exec_res.model_dump(), 1

        policy_res = self.policy_engine.evaluate(proposal)
        exec_res = self.simulator.execute_simulation(
            proposal=proposal,
            policy_result=policy_res,
            is_approved=inp.is_approved,
        )

        return exec_res.model_dump(), 1
