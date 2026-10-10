"""Safe simulation adapter executing remediation strictly against controlled fault engine."""

from __future__ import annotations

import logging
from typing import Any

from app.agent.remediation.actions import (
    PolicyDecision,
    PolicyEvaluationResult,
    RemediationActionType,
    RemediationExecutionResult,
    RemediationProposal,
    SimulationStatus,
)
from app.ingestion.fault_injection import fault_engine

logger = logging.getLogger(__name__)


class RemediationSimulationExecutor:
    """Safely executes permitted remediation actions within the controlled simulation environment."""

    def dry_run(self, proposal: RemediationProposal) -> RemediationExecutionResult:
        """Simulate evaluation of proposal without mutating simulator state."""
        current_fault = fault_engine.get_active_fault_for_service(proposal.target_service)
        pre_state: dict[str, Any] = {
            "target_service": proposal.target_service,
            "has_active_fault": current_fault is not None,
            "active_fault_experiment_id": current_fault.experiment_id if current_fault else None,
        }

        return RemediationExecutionResult(
            status=SimulationStatus.DRY_RUN,
            message=f"Dry-run evaluated successfully for {proposal.action_type.value} on {proposal.target_service}.",
            action_type=proposal.action_type,
            target_service=proposal.target_service,
            pre_execution_state=pre_state,
            post_execution_state=pre_state,
            state_verified=True,
        )

    def execute_simulation(
        self,
        proposal: RemediationProposal,
        policy_result: PolicyEvaluationResult,
        is_approved: bool = False,
    ) -> RemediationExecutionResult:
        """Execute action in simulation only if allowed by policy and approvals."""
        # Hard safety barrier 1: Denied actions can NEVER be executed
        if policy_result.decision == PolicyDecision.DENY:
            return RemediationExecutionResult(
                status=SimulationStatus.SIMULATED_FAILED,
                message=f"Execution blocked: Policy DENIED action. Reasons: {', '.join(policy_result.violations)}",
                action_type=proposal.action_type,
                target_service=proposal.target_service,
                state_verified=False,
                error="POLICY_DENIED",
            )

        # Hard safety barrier 2: Approval-required actions require explicit approval
        if policy_result.decision == PolicyDecision.REQUIRES_APPROVAL and not is_approved:
            return RemediationExecutionResult(
                status=SimulationStatus.SIMULATED_FAILED,
                message="Execution blocked: Action requires operator approval before execution.",
                action_type=proposal.action_type,
                target_service=proposal.target_service,
                state_verified=False,
                error="APPROVAL_REQUIRED",
            )

        # Query state before execution
        initial_fault = fault_engine.get_active_fault_for_service(proposal.target_service)
        pre_state = {
            "target_service": proposal.target_service,
            "has_active_fault": initial_fault is not None,
            "active_experiment_id": initial_fault.experiment_id if initial_fault else None,
        }

        try:
            if proposal.action_type == RemediationActionType.STOP_FAULT_SCENARIO:
                exp_id = proposal.parameters.get("experiment_id")
                if exp_id:
                    fault_engine.deactivate_fault(str(exp_id))
                elif initial_fault:
                    fault_engine.deactivate_fault(initial_fault.experiment_id)

            elif proposal.action_type == RemediationActionType.RESTORE_SERVICE:
                if initial_fault:
                    fault_engine.deactivate_fault(initial_fault.experiment_id)

            elif proposal.action_type == RemediationActionType.MITIGATE_LATENCY_INJECTION:
                new_lat = float(proposal.parameters.get("new_latency_ms", 0.0))
                if initial_fault and "parameters" in initial_fault.__dict__:
                    initial_fault.parameters["added_latency_ms"] = new_lat
                    if new_lat <= 0.0:
                        fault_engine.deactivate_fault(initial_fault.experiment_id)

            elif proposal.action_type == RemediationActionType.RECOMMEND_RUNBOOK_STEP:
                # Recommendation only
                pass

            # Verify post-execution state
            post_fault = fault_engine.get_active_fault_for_service(proposal.target_service)
            post_state = {
                "target_service": proposal.target_service,
                "has_active_fault": post_fault is not None,
                "active_experiment_id": post_fault.experiment_id if post_fault else None,
            }

            state_verified = True
            if proposal.action_type in (
                RemediationActionType.STOP_FAULT_SCENARIO,
                RemediationActionType.RESTORE_SERVICE,
            ):
                # Verify fault is actually deactivated
                state_verified = post_fault is None

            return RemediationExecutionResult(
                status=SimulationStatus.SIMULATED_SUCCESS,
                message=f"Simulated action '{proposal.action_type.value}' executed and verified for {proposal.target_service}.",
                action_type=proposal.action_type,
                target_service=proposal.target_service,
                pre_execution_state=pre_state,
                post_execution_state=post_state,
                state_verified=state_verified,
            )

        except Exception as exc:
            logger.error("Simulation execution failure: %s", exc, exc_info=True)
            return RemediationExecutionResult(
                status=SimulationStatus.SIMULATED_FAILED,
                message=f"Simulation execution error: {str(exc)}",
                action_type=proposal.action_type,
                target_service=proposal.target_service,
                pre_execution_state=pre_state,
                state_verified=False,
                error=str(exc),
            )
