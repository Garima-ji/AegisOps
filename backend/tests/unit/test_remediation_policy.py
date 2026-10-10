"""Unit tests for deterministic remediation policy and safe simulation execution."""

from __future__ import annotations

from app.agent.remediation.actions import (
    PolicyDecision,
    PolicyEvaluationResult,
    RemediationActionType,
    RemediationProposal,
    SimulationStatus,
)
from app.agent.remediation.policy import RemediationPolicyEngine
from app.agent.remediation.simulator import RemediationSimulationExecutor
from app.ingestion.fault_injection import fault_engine


def test_policy_allowlisted_service() -> None:
    """Test policy allows permitted actions on allowlisted services with evidence."""
    engine = RemediationPolicyEngine(require_approval_by_default=False)
    proposal = RemediationProposal(
        action_type=RemediationActionType.RESTORE_SERVICE,
        target_service="payment-service",
        parameters={"service_name": "payment-service"},
        rationale="Clear payment fault",
        supporting_citation_ids=["ev-valid-001"],
    )

    result = engine.evaluate(proposal)
    assert result.decision == PolicyDecision.ALLOW
    assert result.requires_approval is False
    assert len(result.violations) == 0


def test_policy_denies_unallowlisted_service() -> None:
    """Test policy strictly denies actions on unauthorized services."""
    engine = RemediationPolicyEngine(require_approval_by_default=False)
    proposal = RemediationProposal(
        action_type=RemediationActionType.RESTORE_SERVICE,
        target_service="production-user-database",
        parameters={},
        rationale="Malicious or misconfigured target",
        supporting_citation_ids=["ev-valid-001"],
    )

    result = engine.evaluate(proposal)
    assert result.decision == PolicyDecision.DENY
    assert any("prohibited" in v.lower() for v in result.violations)


def test_policy_denies_action_without_supporting_evidence() -> None:
    """Test policy strictly denies execution when no evidence citations are provided."""
    engine = RemediationPolicyEngine(require_approval_by_default=False)
    proposal = RemediationProposal(
        action_type=RemediationActionType.RESTORE_SERVICE,
        target_service="order-service",
        parameters={},
        rationale="Unsubstantiated remediation guess",
        supporting_citation_ids=[],  # Empty citations
    )

    result = engine.evaluate(proposal)
    assert result.decision == PolicyDecision.DENY
    assert any("evidence citation" in v.lower() for v in result.violations)


def test_policy_requires_approval_when_configured() -> None:
    """Test policy gates actions behind approval when approval mode is enabled."""
    engine = RemediationPolicyEngine(require_approval_by_default=True)
    proposal = RemediationProposal(
        action_type=RemediationActionType.STOP_FAULT_SCENARIO,
        target_service="payment-service",
        parameters={"experiment_id": "exp-001"},
        rationale="Stop fault scenario with operator gate",
        supporting_citation_ids=["ev-001"],
    )

    result = engine.evaluate(proposal)
    assert result.decision == PolicyDecision.REQUIRES_APPROVAL
    assert result.requires_approval is True


def test_policy_invalid_parameter_ranges() -> None:
    """Test policy denies out-of-range parameters."""
    engine = RemediationPolicyEngine()
    proposal = RemediationProposal(
        action_type=RemediationActionType.MITIGATE_LATENCY_INJECTION,
        target_service="payment-service",
        parameters={"new_latency_ms": -50.0},  # Negative latency
        rationale="Invalid parameter",
        supporting_citation_ids=["ev-001"],
    )

    result = engine.evaluate(proposal)
    assert result.decision == PolicyDecision.DENY
    assert any("range" in v.lower() for v in result.violations)


def test_simulator_refuses_denied_actions() -> None:
    """Test simulator blocks execution if policy decision was DENY."""
    simulator = RemediationSimulationExecutor()
    proposal = RemediationProposal(
        action_type=RemediationActionType.RESTORE_SERVICE,
        target_service="unauthorized-service",
        parameters={},
        rationale="Blocked action",
        supporting_citation_ids=["ev-001"],
    )
    policy_res = PolicyEvaluationResult(
        decision=PolicyDecision.DENY,
        reason="Prohibited service",
        violations=["Prohibited service"],
        requires_approval=False,
        proposal=proposal,
    )

    exec_res = simulator.execute_simulation(proposal, policy_res, is_approved=True)
    assert exec_res.status == SimulationStatus.SIMULATED_FAILED
    assert exec_res.error == "POLICY_DENIED"


def test_simulator_blocks_unapproved_gated_action() -> None:
    """Test simulator blocks execution if approval is required but not granted."""
    simulator = RemediationSimulationExecutor()
    proposal = RemediationProposal(
        action_type=RemediationActionType.STOP_FAULT_SCENARIO,
        target_service="payment-service",
        parameters={"experiment_id": "exp-test"},
        rationale="Awaiting operator",
        supporting_citation_ids=["ev-001"],
    )
    policy_res = PolicyEvaluationResult(
        decision=PolicyDecision.REQUIRES_APPROVAL,
        reason="Approval required",
        violations=[],
        requires_approval=True,
        proposal=proposal,
    )

    # Not approved
    exec_res = simulator.execute_simulation(proposal, policy_res, is_approved=False)
    assert exec_res.status == SimulationStatus.SIMULATED_FAILED
    assert exec_res.error == "APPROVAL_REQUIRED"


def test_simulator_dry_run_does_not_mutate_state() -> None:
    """Test dry-run evaluation inspects state without changing fault status."""
    fault_engine.reset()
    fault_engine.activate_fault(
        experiment_id="exp-dry-run",
        fault_type="error_surge",
        target_service="payment-service",
        duration_seconds=30,
    )

    simulator = RemediationSimulationExecutor()
    proposal = RemediationProposal(
        action_type=RemediationActionType.STOP_FAULT_SCENARIO,
        target_service="payment-service",
        parameters={"experiment_id": "exp-dry-run"},
        rationale="Dry-run check",
        supporting_citation_ids=["ev-001"],
    )

    dry_res = simulator.dry_run(proposal)
    assert dry_res.status == SimulationStatus.DRY_RUN
    assert dry_res.pre_execution_state["has_active_fault"] is True
    # Fault must remain active after dry-run
    assert fault_engine.get_active_fault_for_service("payment-service") is not None

    fault_engine.reset()
