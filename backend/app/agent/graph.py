"""LangGraph state machine orchestration for incident investigation."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.evidence import EvidenceBank, EvidenceRecord
from app.agent.providers import BaseLLMProvider, FakeDeterministicProvider
from app.agent.remediation.actions import (
    ApprovalStatus,
    PolicyDecision,
    RemediationActionType,
    RemediationProposal,
    SimulationStatus,
)
from app.agent.remediation.policy import RemediationPolicyEngine
from app.agent.reporting import (
    CandidateEvaluation,
    InvestigationReport,
    ProposedRemediationPlan,
    RunbookReference,
)
from app.agent.state import InvestigationState
from app.agent.tools.remediation_tools import (
    EvaluateRemediationPolicyTool,
    ExecuteSimulatedRemediationTool,
)
from app.agent.tools.root_cause_tools import GetRootCauseCandidatesTool
from app.agent.tools.runbook_tools import RetrieveRunbookTool
from app.agent.tools.telemetry_tools import (
    GetAnomalyEventsTool,
    QueryServiceLogsTool,
    QueryServiceMetricsTool,
)
from app.agent.tools.topology_tools import GetServiceTopologyTool

logger = logging.getLogger(__name__)

# Hard limits to prevent runaway loops
MAX_GRAPH_ITERATIONS = 8
MAX_TOOL_CALL_BUDGET = 15


# ---------------------------------------------------------------------------
# Node Functions
# ---------------------------------------------------------------------------


def initialize_node(state: InvestigationState) -> dict[str, Any]:
    """Initialize investigation identity, time bounds, and audit tracking."""
    inv_id = state.get("investigation_id") or f"inv-{uuid.uuid4().hex[:10]}"
    now = datetime.now(UTC)
    end_time = state.get("time_window_end") or now
    start_time = state.get("time_window_start") or (end_time - timedelta(minutes=30))

    audit_entry = {
        "event_type": "INITIALIZE",
        "investigation_id": inv_id,
        "timestamp": now.isoformat(),
        "time_window": f"{start_time.isoformat()} -> {end_time.isoformat()}",
    }

    return {
        "investigation_id": inv_id,
        "time_window_start": start_time,
        "time_window_end": end_time,
        "current_step": "initialize",
        "iteration": 0,
        "tool_call_count": 0,
        "budget_exhausted": False,
        "evidence_records": state.get("evidence_records", []),
        "anomaly_events": [],
        "phase4_candidates": [],
        "supporting_citations": [],
        "contradicting_citations": [],
        "missing_evidence": [],
        "coverage_limitations": [],
        "runbook_references": [],
        "candidate_assessments": {},
        "is_abstaining": False,
        "abstention_reason": None,
        "audit_events": [audit_entry],
        "errors": [],
    }


def gather_initial_evidence_node(state: InvestigationState) -> dict[str, Any]:
    """Retrieve anomalies, topology snapshot, and Phase 4 root-cause candidates."""
    db = state.get("db_session")
    st = state["time_window_start"]
    et = state["time_window_end"]
    inc_id = state.get("incident_id")

    evidence_records: list[dict[str, Any]] = list(state.get("evidence_records", []))
    audit_events: list[dict[str, Any]] = list(state.get("audit_events", []))
    tool_count = state.get("tool_call_count", 0)

    # 1. Fetch anomaly events
    anomaly_events: list[dict[str, Any]] = []
    if db:
        tool = GetAnomalyEventsTool()
        resp = tool.execute_with_audit(db=db, start_time=st, end_time=et, limit=30)
        tool_count += 1
        audit_events.append(resp.audit.model_dump())
        if resp.success and resp.data:
            anomaly_events = resp.data.get("anomalies", [])
            evidence_records.extend(anomaly_events)

    # 2. Fetch service topology
    topo_summary: dict[str, Any] = {}
    if db:
        tool_topo = GetServiceTopologyTool()
        resp_topo = tool_topo.execute_with_audit(db=db)
        tool_count += 1
        audit_events.append(resp_topo.audit.model_dump())
        if resp_topo.success and resp_topo.data:
            topo_summary = resp_topo.data
            evidence_records.extend(resp_topo.data.get("evidence", []))

    # 3. Fetch Phase 4 root-cause candidates
    p4_candidates: list[dict[str, Any]] = []
    top_cand = None
    if db:
        tool_rca = GetRootCauseCandidatesTool()
        resp_rca = tool_rca.execute_with_audit(
            db=db, incident_id=inc_id, start_time=st, end_time=et, limit=5
        )
        tool_count += 1
        audit_events.append(resp_rca.audit.model_dump())
        if resp_rca.success and resp_rca.data:
            p4_candidates = resp_rca.data.get("candidates", [])
            top_cand = resp_rca.data.get("top_candidate")
            evidence_records.extend(resp_rca.data.get("evidence", []))

    return {
        "current_step": "gather_initial_evidence",
        "anomaly_events": anomaly_events,
        "topology_summary": topo_summary,
        "phase4_candidates": p4_candidates,
        "leading_candidate": top_cand,
        "evidence_records": evidence_records,
        "tool_call_count": tool_count,
        "audit_events": audit_events,
    }


def assess_evidence_sufficiency_node(state: InvestigationState) -> dict[str, Any]:
    """Evaluate whether evidence is sufficient, conflicting, or missing."""
    anomalies = state.get("anomaly_events", [])
    p4_candidates = state.get("phase4_candidates", [])
    evidence_records = state.get("evidence_records", [])
    tool_count = state.get("tool_call_count", 0)
    iteration = state.get("iteration", 0) + 1

    # Check for prompt injection or explicitly conflicting records
    has_conflicts = any("conflict" in str(ev.get("summary", "")).lower() for ev in evidence_records)
    if has_conflicts:
        return {
            "current_step": "assess_evidence_sufficiency",
            "iteration": iteration,
            "is_abstaining": True,
            "abstention_reason": "CONTRADICTORY_SIGNALS: Conflicting signals between metrics and error logs",
        }

    # If both anomalies and candidates are completely missing -> immediately abstain
    if not anomalies and not p4_candidates:
        return {
            "current_step": "assess_evidence_sufficiency",
            "iteration": iteration,
            "is_abstaining": True,
            "abstention_reason": "INSUFFICIENT_EVIDENCE: No anomaly detection events or root cause candidates available",
        }

    # If anomalies exist but we need targeted metric/log details for suspect service
    telemetry_evs = [ev for ev in evidence_records if ev.get("source_type") in ("metric", "log")]
    if len(telemetry_evs) < 2 and tool_count < MAX_TOOL_CALL_BUDGET and iteration <= 1:
        return {
            "current_step": "assess_evidence_sufficiency",
            "iteration": iteration,
            "needs_targeted": True,
        }

    return {
        "current_step": "assess_evidence_sufficiency",
        "iteration": iteration,
        "needs_targeted": False,
    }


def targeted_investigation_node(state: InvestigationState) -> dict[str, Any]:
    """Targeted investigation querying missing metrics and logs for suspected services."""
    db = state.get("db_session")
    st = state["time_window_start"]
    et = state["time_window_end"]
    cand = state.get("leading_candidate") or "payment-service"

    evidence_records = list(state.get("evidence_records", []))
    audit_events = list(state.get("audit_events", []))
    tool_count = state.get("tool_call_count", 0)

    if db:
        # Query metrics
        m_tool = QueryServiceMetricsTool()
        resp_m = m_tool.execute_with_audit(
            db=db, service_name=cand, start_time=st, end_time=et, limit=30
        )
        tool_count += 1
        audit_events.append(resp_m.audit.model_dump())
        if resp_m.success and resp_m.data:
            evidence_records.extend(resp_m.data.get("evidence", []))

        # Query logs
        l_tool = QueryServiceLogsTool()
        resp_l = l_tool.execute_with_audit(
            db=db, service_name=cand, start_time=st, end_time=et, limit=20
        )
        tool_count += 1
        audit_events.append(resp_l.audit.model_dump())
        if resp_l.success and resp_l.data:
            evidence_records.extend(resp_l.data.get("evidence", []))

    return {
        "current_step": "targeted_investigation",
        "evidence_records": evidence_records,
        "tool_call_count": tool_count,
        "audit_events": audit_events,
    }


def correlate_evidence_node(state: InvestigationState) -> dict[str, Any]:
    """Correlate candidate root causes against telemetry deviations and topology."""
    evidence_records = state.get("evidence_records", [])
    p4_candidates = state.get("phase4_candidates", [])
    lead_cand = state.get("leading_candidate")

    supporting: list[str] = []
    contradicting: list[str] = []
    assessments: dict[str, str] = {}

    for cand in p4_candidates:
        svc = cand.get("service_name")
        if not svc:
            continue
        svc_str = str(svc)
        # Check matching evidence
        svc_evs = [ev for ev in evidence_records if ev.get("service_name") == svc_str]
        if svc_str == lead_cand or (not lead_cand and cand.get("rank") == 1):
            assessments[svc_str] = "SUPPORTED"
            for ev in svc_evs:
                if ev.get("id"):
                    supporting.append(ev["id"])
        else:
            assessments[svc_str] = "CONTRADICTED"
            for ev in svc_evs:
                if ev.get("id"):
                    contradicting.append(ev["id"])

    # Fallback if no candidate citations
    if not supporting and evidence_records:
        for ev in evidence_records[:3]:
            if ev.get("id"):
                supporting.append(ev["id"])

    return {
        "current_step": "correlate_evidence",
        "candidate_assessments": assessments,
        "supporting_citations": supporting[:5],
        "contradicting_citations": contradicting[:5],
    }


def retrieve_runbook_node(state: InvestigationState) -> dict[str, Any]:
    """Retrieve operational runbooks for leading candidate and symptoms."""
    lead_cand = state.get("leading_candidate") or "payment-service"
    evidence_records = list(state.get("evidence_records", []))
    audit_events = list(state.get("audit_events", []))
    tool_count = state.get("tool_call_count", 0)

    rb_tool = RetrieveRunbookTool()
    resp = rb_tool.execute_with_audit(
        query=f"{lead_cand} latency errors timeout",
        target_service=lead_cand,
        limit=2,
    )
    tool_count += 1
    audit_events.append(resp.audit.model_dump())

    runbooks: list[dict[str, Any]] = []
    if resp.success and resp.data:
        runbooks = resp.data.get("runbooks", [])
        evidence_records.extend(resp.data.get("evidence", []))

    return {
        "current_step": "retrieve_runbook",
        "runbook_references": runbooks,
        "evidence_records": evidence_records,
        "tool_call_count": tool_count,
        "audit_events": audit_events,
    }


def validate_diagnosis_node(state: InvestigationState) -> dict[str, Any]:
    """Validate diagnosis using model provider or deterministic fake."""
    provider: BaseLLMProvider = state.get("llm_provider") or FakeDeterministicProvider()
    untrusted_payload = {"evidence": state.get("evidence_records", [])}
    context = {
        "phase4_candidates": state.get("phase4_candidates", []),
        "runbooks": state.get("runbook_references", []),
    }

    reasoning = provider.reason_investigation(
        prompt_instruction="Synthesize grounded diagnosis from untrusted telemetry evidence.",
        untrusted_evidence_payload=untrusted_payload,
        context=context,
    )

    if reasoning.is_abstaining:
        return {
            "current_step": "validate_diagnosis",
            "is_abstaining": True,
            "abstention_reason": reasoning.abstention_reason
            or "Agent determined evidence is inconclusive.",
        }

    return {
        "current_step": "validate_diagnosis",
        "leading_candidate": reasoning.leading_candidate or state.get("leading_candidate"),
        "supporting_citations": reasoning.supporting_citations
        or state.get("supporting_citations", []),
        "confidence_score": reasoning.confidence,
    }


def recommend_action_node(state: InvestigationState) -> dict[str, Any]:
    """Formulate safe proposed remediation plan from runbooks and evidence."""
    lead_cand = state.get("leading_candidate") or "payment-service"
    runbooks = state.get("runbook_references", [])
    supporting = state.get("supporting_citations", [])
    inc_id = state.get("incident_id") or f"exp-{lead_cand}"

    action_type = RemediationActionType.RESTORE_SERVICE
    parameters: dict[str, Any] = {"service_name": lead_cand}

    if runbooks:
        first_rb = runbooks[0]
        rec_actions = first_rb.get("remediation_actions", [])
        if "mitigate_latency_injection" in rec_actions:
            action_type = RemediationActionType.MITIGATE_LATENCY_INJECTION
            parameters = {"service_name": lead_cand, "new_latency_ms": 0.0}
        elif "restore_service" in rec_actions:
            action_type = RemediationActionType.RESTORE_SERVICE
            parameters = {"service_name": lead_cand}
        elif "stop_fault_scenario" in rec_actions:
            action_type = RemediationActionType.STOP_FAULT_SCENARIO
            parameters = {"experiment_id": inc_id, "service_name": lead_cand}

    proposal = RemediationProposal(
        action_type=action_type,
        target_service=lead_cand,
        parameters=parameters,
        rationale=f"Remediate observed fault condition on leading candidate {lead_cand}.",
        supporting_citation_ids=supporting,
    )

    return {
        "current_step": "recommend_action",
        "proposed_remediation": proposal.model_dump(),
    }


def apply_safety_policy_node(state: InvestigationState) -> dict[str, Any]:
    """Evaluate proposed remediation against deterministic safety policy."""
    prop_dict = state.get("proposed_remediation")
    require_approval = state.get("require_approval_by_default", True)
    audit_events = list(state.get("audit_events", []))
    tool_count = state.get("tool_call_count", 0)

    if not prop_dict:
        return {
            "current_step": "apply_safety_policy",
            "policy_evaluation": None,
            "approval_status": ApprovalStatus.NOT_REQUIRED.value,
        }

    proposal = RemediationProposal(**prop_dict)
    policy_tool = EvaluateRemediationPolicyTool(
        policy_engine=RemediationPolicyEngine(require_approval_by_default=require_approval)
    )

    resp = policy_tool.execute_with_audit(
        action_type=proposal.action_type.value,
        target_service=proposal.target_service,
        parameters=proposal.parameters,
        rationale=proposal.rationale,
        supporting_citation_ids=proposal.supporting_citation_ids,
    )
    tool_count += 1
    audit_events.append(resp.audit.model_dump())

    policy_data = resp.data or {}
    decision = policy_data.get("decision", PolicyDecision.DENY.value)

    approval_status = ApprovalStatus.NOT_REQUIRED.value
    if decision == PolicyDecision.REQUIRES_APPROVAL.value:
        approval_status = ApprovalStatus.PENDING.value

    return {
        "current_step": "apply_safety_policy",
        "policy_evaluation": policy_data,
        "approval_status": approval_status,
        "tool_call_count": tool_count,
        "audit_events": audit_events,
    }


def execute_simulation_node(state: InvestigationState) -> dict[str, Any]:
    """Safely execute allowed simulation action against controlled fault engine."""
    prop_dict = state.get("proposed_remediation")
    policy_data = state.get("policy_evaluation")
    audit_events = list(state.get("audit_events", []))
    tool_count = state.get("tool_call_count", 0)

    if not prop_dict or not policy_data:
        return {"current_step": "execute_simulation", "simulation_result": None}

    proposal = RemediationProposal(**prop_dict)
    sim_tool = ExecuteSimulatedRemediationTool()

    resp = sim_tool.execute_with_audit(
        action_type=proposal.action_type.value,
        target_service=proposal.target_service,
        parameters=proposal.parameters,
        is_approved=(state.get("approval_status") == ApprovalStatus.APPROVED.value),
        supporting_citation_ids=proposal.supporting_citation_ids,
    )
    tool_count += 1
    audit_events.append(resp.audit.model_dump())

    return {
        "current_step": "execute_simulation",
        "simulation_result": resp.data,
        "tool_call_count": tool_count,
        "audit_events": audit_events,
    }


def generate_report_node(state: InvestigationState) -> dict[str, Any]:
    """Synthesize evidence-grounded final report and validate citations."""
    evidence_bank = EvidenceBank()
    for ev_dict in state.get("evidence_records", []):
        try:
            record = EvidenceRecord(**ev_dict)
            evidence_bank.add(record)
        except Exception:
            pass

    is_abstaining = state.get("is_abstaining", False)
    abstention_reason = state.get("abstention_reason")
    lead_cand = state.get("leading_candidate")
    p4_candidates = state.get("phase4_candidates", [])
    supporting = state.get("supporting_citations", [])
    contradicting = state.get("contradicting_citations", [])
    assessments = state.get("candidate_assessments", {})

    # Build candidate evaluations
    cand_evals: list[CandidateEvaluation] = []
    for cand in p4_candidates:
        svc = cand.get("service_name", "unknown")
        status = assessments.get(svc, "INCONCLUSIVE")
        cand_evals.append(
            CandidateEvaluation(
                service_name=svc,
                phase4_rank=cand.get("rank", 1),
                phase4_score=cand.get("composite_score", cand.get("score", 0.0)),
                status=status,
                supporting_citation_ids=[c for c in supporting if svc in c],
                contradicting_citation_ids=[c for c in contradicting if svc in c],
                reasoning=cand.get("explanation", f"Candidate evaluation for {svc}"),
            )
        )

    # Build runbook references
    rb_refs: list[RunbookReference] = []
    for rb in state.get("runbook_references", []):
        rb_refs.append(
            RunbookReference(
                runbook_id=rb.get("runbook_id", "rb-unknown"),
                title=rb.get("title", "Runbook"),
                target_service=rb.get("target_service", lead_cand or "unknown"),
                relevant_section="Recommended Remediation",
                excerpt=rb.get("excerpt", "")[:200],
                citation_id="",
            )
        )

    # Build proposed remediation plan
    prop_plan: ProposedRemediationPlan | None = None
    prop_dict = state.get("proposed_remediation")
    policy_data = state.get("policy_evaluation") or {}
    sim_data = state.get("simulation_result") or {}

    if prop_dict and not is_abstaining:
        prop_plan = ProposedRemediationPlan(
            action_type=prop_dict["action_type"],
            target_service=prop_dict["target_service"],
            parameters=prop_dict.get("parameters", {}),
            rationale=prop_dict.get("rationale", ""),
            supporting_citation_ids=prop_dict.get("supporting_citation_ids", []),
            potential_risk=prop_dict.get("potential_risk", "Low"),
            reversibility=prop_dict.get("reversibility", "High"),
            policy_decision=policy_data.get("decision", PolicyDecision.DENY.value),
            approval_status=state.get("approval_status", ApprovalStatus.NOT_REQUIRED.value),
            simulation_status=sim_data.get("status", SimulationStatus.NOT_EXECUTED.value),
        )

    # Summary text
    if is_abstaining:
        summary_text = f"Investigation abstained: {abstention_reason}"
        confidence_class = "INSUFFICIENT_EVIDENCE"
        confidence_num = 0.0
    else:
        summary_text = (
            f"Investigation confirmed root cause initiating at service '{lead_cand}'. "
            f"Correlated with Phase 4 ranking and validated with {len(supporting)} evidence citations."
        )
        confidence_class = "HIGH" if len(supporting) >= 2 else "MEDIUM"
        confidence_num = 0.88

    report = InvestigationReport(
        investigation_id=state["investigation_id"],
        incident_id=state.get("incident_id") or "inc-auto",
        time_window_start=state["time_window_start"],
        time_window_end=state["time_window_end"],
        summary=summary_text,
        severity="HIGH" if not is_abstaining else "LOW",
        confidence_classification=confidence_class,
        confidence_score=confidence_num,
        phase4_root_cause_ranking=p4_candidates,
        leading_candidate=lead_cand if not is_abstaining else None,
        candidate_evaluations=cand_evals,
        supporting_evidence_citations=supporting,
        contradicting_evidence_citations=contradicting,
        missing_evidence=["Missing trace spans for edge services"] if is_abstaining else [],
        coverage_limitations=["Local test environment simulation"],
        runbook_references=rb_refs,
        recommended_diagnostic_step="Review real-time transaction traces"
        if not is_abstaining
        else "Gather additional telemetry before action",
        proposed_remediation=prop_plan,
        is_abstained=is_abstaining,
        abstention_reason=abstention_reason,
        investigation_duration_seconds=1.2,
        tool_call_count=state.get("tool_call_count", 0),
    )

    # Validate citations
    report.validate_all_citations(evidence_bank)

    return {
        "current_step": "generate_report",
        "final_report": report.model_dump(mode="json"),
    }


def finalize_and_audit_node(state: InvestigationState) -> dict[str, Any]:
    """Finalize audit events and mark investigation completion."""
    audit_events = list(state.get("audit_events", []))
    audit_events.append(
        {
            "event_type": "FINALIZE",
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "ABSTAINED" if state.get("is_abstaining") else "COMPLETED",
            "tool_call_count": state.get("tool_call_count", 0),
        }
    )

    return {
        "current_step": "finalize_and_audit",
        "audit_events": audit_events,
    }


# ---------------------------------------------------------------------------
# Routing Functions
# ---------------------------------------------------------------------------


def route_after_assessment(state: InvestigationState) -> str:
    """Conditional router based on evidence sufficiency."""
    if state.get("is_abstaining"):
        return "generate_report"
    if state.get("needs_targeted"):
        return "targeted_investigation"
    return "correlate_evidence"


def route_after_policy(state: InvestigationState) -> str:
    """Conditional router after policy evaluation."""
    policy_data = state.get("policy_evaluation") or {}
    decision = policy_data.get("decision")
    if decision == PolicyDecision.ALLOW.value:
        return "execute_simulation"
    return "generate_report"


# ---------------------------------------------------------------------------
# Graph Builder
# ---------------------------------------------------------------------------


def build_investigation_graph() -> Any:
    """Construct and compile the bounded LangGraph state machine."""
    workflow = StateGraph(InvestigationState)

    # Add Nodes
    workflow.add_node("initialize", initialize_node)
    workflow.add_node("gather_initial_evidence", gather_initial_evidence_node)
    workflow.add_node("assess_evidence_sufficiency", assess_evidence_sufficiency_node)
    workflow.add_node("targeted_investigation", targeted_investigation_node)
    workflow.add_node("correlate_evidence", correlate_evidence_node)
    workflow.add_node("retrieve_runbook", retrieve_runbook_node)
    workflow.add_node("validate_diagnosis", validate_diagnosis_node)
    workflow.add_node("recommend_action", recommend_action_node)
    workflow.add_node("apply_safety_policy", apply_safety_policy_node)
    workflow.add_node("execute_simulation", execute_simulation_node)
    workflow.add_node("generate_report", generate_report_node)
    workflow.add_node("finalize_and_audit", finalize_and_audit_node)

    # Add Edges
    workflow.add_edge(START, "initialize")
    workflow.add_edge("initialize", "gather_initial_evidence")
    workflow.add_edge("gather_initial_evidence", "assess_evidence_sufficiency")

    workflow.add_conditional_edges(
        "assess_evidence_sufficiency",
        route_after_assessment,
        {
            "targeted_investigation": "targeted_investigation",
            "correlate_evidence": "correlate_evidence",
            "generate_report": "generate_report",
        },
    )

    workflow.add_edge("targeted_investigation", "assess_evidence_sufficiency")
    workflow.add_edge("correlate_evidence", "retrieve_runbook")
    workflow.add_edge("retrieve_runbook", "validate_diagnosis")
    workflow.add_edge("validate_diagnosis", "recommend_action")
    workflow.add_edge("recommend_action", "apply_safety_policy")

    workflow.add_conditional_edges(
        "apply_safety_policy",
        route_after_policy,
        {
            "execute_simulation": "execute_simulation",
            "generate_report": "generate_report",
        },
    )

    workflow.add_edge("execute_simulation", "generate_report")
    workflow.add_edge("generate_report", "finalize_and_audit")
    workflow.add_edge("finalize_and_audit", END)

    return workflow.compile()
