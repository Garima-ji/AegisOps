"""Service orchestrating agentic investigations, persistence, and safe remediation lifecycles."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.graph import build_investigation_graph
from app.agent.providers import BaseLLMProvider, ConfigurableLLMProvider
from app.agent.remediation.actions import (
    ApprovalStatus,
    PolicyDecision,
    RemediationActionType,
    RemediationProposal,
    SimulationStatus,
)
from app.agent.remediation.policy import RemediationPolicyEngine
from app.agent.remediation.simulator import RemediationSimulationExecutor
from app.core.config import get_settings
from app.db.models.investigation import (
    InvestigationEventModel,
    InvestigationModel,
    RemediationProposalModel,
)
from app.topology.models import _to_utc

logger = logging.getLogger(__name__)


def _json_safe(obj: Any) -> Any:
    """Ensure data is strictly JSON-serializable for database persistence."""
    if obj is None:
        return None
    return json.loads(json.dumps(obj, default=str))


class InvestigationService:
    """Orchestration service for agentic incident investigations and policy-controlled remediation."""

    def __init__(self, llm_provider: BaseLLMProvider | None = None) -> None:
        settings = get_settings()
        self.llm_provider = llm_provider or ConfigurableLLMProvider(
            provider_name=settings.agent_llm_provider,
            model_name=settings.agent_llm_model,
            api_key=settings.agent_llm_api_key.get_secret_value(),
        )
        self.graph = build_investigation_graph()
        self.policy_engine = RemediationPolicyEngine(
            require_approval_by_default=settings.remediation_require_approval_by_default
        )
        self.simulator = RemediationSimulationExecutor()

    def run_investigation(
        self,
        db: Session,
        incident_id: str | None = None,
        time_window_start: datetime | None = None,
        time_window_end: datetime | None = None,
        require_approval: bool | None = None,
    ) -> InvestigationModel:
        """Execute the LangGraph investigation workflow end-to-end and persist results."""
        settings = get_settings()
        now = datetime.now(UTC)
        et = _to_utc(time_window_end) if time_window_end else now
        st = _to_utc(time_window_start) if time_window_start else (et - timedelta(minutes=30))

        if st >= et:
            raise ValueError(
                f"start_time ({st.isoformat()}) must precede end_time ({et.isoformat()})"
            )

        req_approval = (
            require_approval
            if require_approval is not None
            else settings.remediation_require_approval_by_default
        )

        inv_id = f"inv-{uuid.uuid4().hex[:12]}"
        initial_state = {
            "investigation_id": inv_id,
            "incident_id": incident_id or f"inc-{uuid.uuid4().hex[:8]}",
            "time_window_start": st,
            "time_window_end": et,
            "db_session": db,
            "llm_provider": self.llm_provider,
            "require_approval_by_default": req_approval,
        }

        # Execute LangGraph state machine
        final_state = self.graph.invoke(initial_state)

        # Build database investigation record
        final_report = final_state.get("final_report") or {}
        is_abstained = final_state.get("is_abstaining", False)
        status = "ABSTAINED" if is_abstained else "COMPLETED"

        inv_record = InvestigationModel(
            id=inv_id,
            incident_id=final_state.get("incident_id"),
            status=status,
            time_window_start=st,
            time_window_end=et,
            leading_candidate=final_state.get("leading_candidate"),
            confidence_score=final_report.get("confidence_score", 0.0),
            is_abstained=is_abstained,
            abstention_reason=final_state.get("abstention_reason"),
            report_payload=_json_safe(final_report),
            tool_call_count=final_state.get("tool_call_count", 0),
            created_at=now,
            updated_at=now,
        )
        db.add(inv_record)
        db.flush()

        # Persist audit trail events
        audit_events = final_state.get("audit_events", [])
        for evt in audit_events:
            evt_type = evt.get("event_type", evt.get("tool_name", "UNKNOWN"))
            event_rec = InvestigationEventModel(
                id=f"evt-{uuid.uuid4().hex[:12]}",
                investigation_id=inv_id,
                event_type=evt_type,
                payload=_json_safe(evt),
                timestamp=datetime.now(UTC),
            )
            db.add(event_rec)

        # Persist remediation proposal if generated
        prop_dict = final_state.get("proposed_remediation")
        policy_data = final_state.get("policy_evaluation")
        sim_data = final_state.get("simulation_result")

        if prop_dict and not is_abstained:
            rem_rec = RemediationProposalModel(
                id=f"rem-{uuid.uuid4().hex[:12]}",
                investigation_id=inv_id,
                action_type=prop_dict["action_type"],
                target_service=prop_dict["target_service"],
                parameters=_json_safe(prop_dict.get("parameters")),
                policy_decision=policy_data.get("decision", PolicyDecision.DENY.value)
                if policy_data
                else PolicyDecision.DENY.value,
                approval_status=final_state.get(
                    "approval_status", ApprovalStatus.NOT_REQUIRED.value
                ),
                simulation_status=sim_data.get("status", SimulationStatus.NOT_EXECUTED.value)
                if sim_data
                else SimulationStatus.NOT_EXECUTED.value,
                simulation_result=_json_safe(sim_data),
                created_at=now,
            )
            db.add(rem_rec)

        db.commit()
        db.refresh(inv_record)
        return inv_record

    def get_investigation(self, investigation_id: str, db: Session) -> InvestigationModel | None:
        """Retrieve investigation run by ID."""
        stmt = select(InvestigationModel).where(InvestigationModel.id == investigation_id).limit(1)
        return db.execute(stmt).scalars().first()

    def get_events(
        self, investigation_id: str, db: Session, limit: int = 50
    ) -> list[InvestigationEventModel]:
        """Retrieve sanitized chronological audit events for an investigation."""
        stmt = (
            select(InvestigationEventModel)
            .where(InvestigationEventModel.investigation_id == investigation_id)
            .order_by(InvestigationEventModel.timestamp.asc())
            .limit(limit)
        )
        return list(db.execute(stmt).scalars().all())

    def get_proposal_for_investigation(
        self, investigation_id: str, db: Session
    ) -> RemediationProposalModel | None:
        """Retrieve latest remediation proposal for an investigation."""
        stmt = (
            select(RemediationProposalModel)
            .where(RemediationProposalModel.investigation_id == investigation_id)
            .order_by(RemediationProposalModel.created_at.desc())
            .limit(1)
        )
        return db.execute(stmt).scalars().first()

    def dry_run_remediation(self, investigation_id: str, db: Session) -> dict[str, Any]:
        """Perform dry-run safety evaluation of remediation without state mutation."""
        proposal_rec = self.get_proposal_for_investigation(investigation_id, db)
        if not proposal_rec:
            raise ValueError(
                f"No remediation proposal found for investigation '{investigation_id}'."
            )

        proposal = RemediationProposal(
            action_type=RemediationActionType(proposal_rec.action_type),
            target_service=proposal_rec.target_service,
            parameters=proposal_rec.parameters or {},
            rationale="API dry-run evaluation",
            supporting_citation_ids=[],
        )

        exec_res = self.simulator.dry_run(proposal)
        return exec_res.model_dump()

    def approve_remediation(
        self, investigation_id: str, db: Session, operator_id: str = "operator"
    ) -> RemediationProposalModel:
        """Record explicit operator approval for a pending remediation proposal."""
        proposal_rec = self.get_proposal_for_investigation(investigation_id, db)
        if not proposal_rec:
            raise ValueError(
                f"No remediation proposal found for investigation '{investigation_id}'."
            )

        if proposal_rec.policy_decision == PolicyDecision.DENY.value:
            raise ValueError(
                "Cannot approve action that was DENIED by deterministic safety policy."
            )

        proposal_rec.approval_status = ApprovalStatus.APPROVED.value

        # Record audit event
        audit_rec = InvestigationEventModel(
            id=f"evt-{uuid.uuid4().hex[:12]}",
            investigation_id=investigation_id,
            event_type="REMEDIATION_APPROVED",
            payload={"operator_id": operator_id, "proposal_id": proposal_rec.id},
            timestamp=datetime.now(UTC),
        )
        db.add(audit_rec)
        db.commit()
        db.refresh(proposal_rec)
        return proposal_rec

    def execute_simulation(self, investigation_id: str, db: Session) -> RemediationProposalModel:
        """Execute approved or policy-allowed simulation action within the controlled simulator."""
        proposal_rec = self.get_proposal_for_investigation(investigation_id, db)
        if not proposal_rec:
            raise ValueError(
                f"No remediation proposal found for investigation '{investigation_id}'."
            )

        proposal = RemediationProposal(
            action_type=RemediationActionType(proposal_rec.action_type),
            target_service=proposal_rec.target_service,
            parameters=proposal_rec.parameters or {},
            rationale="Simulated execution",
            supporting_citation_ids=["ev-sim-approved"],
        )

        policy_res = self.policy_engine.evaluate(proposal, require_approval_override=False)
        is_approved = (proposal_rec.approval_status == ApprovalStatus.APPROVED.value) or (
            proposal_rec.policy_decision == PolicyDecision.ALLOW.value
        )

        exec_res = self.simulator.execute_simulation(
            proposal=proposal,
            policy_result=policy_res,
            is_approved=is_approved,
        )

        proposal_rec.simulation_status = exec_res.status.value
        proposal_rec.simulation_result = _json_safe(exec_res.model_dump())

        audit_rec = InvestigationEventModel(
            id=f"evt-{uuid.uuid4().hex[:12]}",
            investigation_id=investigation_id,
            event_type="REMEDIATION_SIMULATED",
            payload=_json_safe(exec_res.model_dump()),
            timestamp=datetime.now(UTC),
        )
        db.add(audit_rec)
        db.commit()
        db.refresh(proposal_rec)
        return proposal_rec
