"""FastAPI endpoints for agentic incident investigation, report retrieval, and safe remediation."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.service import InvestigationService
from app.db.session import get_db

logger = logging.getLogger("aegisops.investigations.api")

router = APIRouter(prefix="/investigations", tags=["Incident Investigation & Remediation"])

investigation_service = InvestigationService()


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------


class InvestigationStartRequest(BaseModel):
    """Payload to initiate an agentic incident investigation."""

    incident_id: str | None = Field(
        default=None, description="Optional associated incident identifier"
    )
    time_window_start: datetime | None = Field(
        default=None, description="Start of incident observation window"
    )
    time_window_end: datetime | None = Field(
        default=None, description="End of incident observation window"
    )
    require_approval: bool | None = Field(
        default=None, description="Override default remediation approval requirement"
    )


class InvestigationSummaryResponse(BaseModel):
    """Summary of an investigation run status and findings."""

    id: str
    incident_id: str | None
    status: str
    time_window_start: datetime
    time_window_end: datetime
    leading_candidate: str | None
    confidence_score: float
    is_abstained: bool
    abstention_reason: str | None
    tool_call_count: int
    created_at: datetime


class InvestigationEventResponse(BaseModel):
    """Audit ledger entry for an investigation step or tool invocation."""

    id: str
    investigation_id: str
    event_type: str
    payload: dict[str, Any] | None
    timestamp: datetime


class RemediationProposalResponse(BaseModel):
    """Remediation proposal details and simulation status."""

    id: str
    investigation_id: str
    action_type: str
    target_service: str
    parameters: dict[str, Any] | None
    policy_decision: str
    approval_status: str
    simulation_status: str
    simulation_result: dict[str, Any] | None
    created_at: datetime


class RemediationApproveRequest(BaseModel):
    """Operator sign-off request payload."""

    operator_id: str = Field(
        default="operator", description="Operator identity signing off on action"
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=InvestigationSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start Agentic Investigation",
    description="Initiates an agentic investigation over an incident window orchestrated via LangGraph.",
)
def start_investigation(
    payload: InvestigationStartRequest,
    session: Session = Depends(get_db),
) -> InvestigationSummaryResponse:
    """Trigger agentic investigation workflow."""
    try:
        record = investigation_service.run_investigation(
            db=session,
            incident_id=payload.incident_id,
            time_window_start=payload.time_window_start,
            time_window_end=payload.time_window_end,
            require_approval=payload.require_approval,
        )
        return InvestigationSummaryResponse(
            id=record.id,
            incident_id=record.incident_id,
            status=record.status,
            time_window_start=record.time_window_start,
            time_window_end=record.time_window_end,
            leading_candidate=record.leading_candidate,
            confidence_score=record.confidence_score,
            is_abstained=record.is_abstained,
            abstention_reason=record.abstention_reason,
            tool_call_count=record.tool_call_count,
            created_at=record.created_at,
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        ) from val_err
    except Exception as exc:
        logger.exception("Investigation launch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete incident investigation.",
        ) from exc


@router.get(
    "/{investigation_id}",
    response_model=InvestigationSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Investigation Summary",
    description="Retrieve the current status and high-level findings of an investigation by ID.",
)
def get_investigation(
    investigation_id: str,
    session: Session = Depends(get_db),
) -> InvestigationSummaryResponse:
    """Retrieve investigation run by ID."""
    record = investigation_service.get_investigation(investigation_id, session)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation '{investigation_id}' not found.",
        )
    return InvestigationSummaryResponse(
        id=record.id,
        incident_id=record.incident_id,
        status=record.status,
        time_window_start=record.time_window_start,
        time_window_end=record.time_window_end,
        leading_candidate=record.leading_candidate,
        confidence_score=record.confidence_score,
        is_abstained=record.is_abstained,
        abstention_reason=record.abstention_reason,
        tool_call_count=record.tool_call_count,
        created_at=record.created_at,
    )


@router.get(
    "/{investigation_id}/report",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Get Investigation Report",
    description="Retrieve the fully validated, evidence-grounded structured report for an investigation.",
)
def get_investigation_report(
    investigation_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve validated investigation report JSON."""
    record = investigation_service.get_investigation(investigation_id, session)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation '{investigation_id}' not found.",
        )
    if not record.report_payload:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation report is not yet generated or available.",
        )
    return record.report_payload


@router.get(
    "/{investigation_id}/events",
    response_model=list[InvestigationEventResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Investigation Audit Events",
    description="Retrieve bounded, sanitized audit events for an investigation run.",
)
def get_investigation_events(
    investigation_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> list[InvestigationEventResponse]:
    """Retrieve audit events timeline."""
    record = investigation_service.get_investigation(investigation_id, session)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation '{investigation_id}' not found.",
        )
    events = investigation_service.get_events(investigation_id, session, limit=limit)
    return [
        InvestigationEventResponse(
            id=e.id,
            investigation_id=e.investigation_id,
            event_type=e.event_type,
            payload=e.payload,
            timestamp=e.timestamp,
        )
        for e in events
    ]


@router.post(
    "/{investigation_id}/remediation/dry-run",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Dry-Run Remediation Action",
    description="Evaluate proposed remediation against policy without mutating simulator state.",
)
def dry_run_remediation(
    investigation_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Dry-run proposed remediation."""
    try:
        return investigation_service.dry_run_remediation(investigation_id, session)
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        ) from val_err


@router.post(
    "/{investigation_id}/remediation/approve",
    response_model=RemediationProposalResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Remediation Action",
    description="Record explicit operator approval for a pending remediation proposal.",
)
def approve_remediation(
    investigation_id: str,
    payload: RemediationApproveRequest,
    session: Session = Depends(get_db),
) -> RemediationProposalResponse:
    """Record operator approval."""
    try:
        proposal = investigation_service.approve_remediation(
            investigation_id=investigation_id,
            db=session,
            operator_id=payload.operator_id,
        )
        return RemediationProposalResponse(
            id=proposal.id,
            investigation_id=proposal.investigation_id,
            action_type=proposal.action_type,
            target_service=proposal.target_service,
            parameters=proposal.parameters,
            policy_decision=proposal.policy_decision,
            approval_status=proposal.approval_status,
            simulation_status=proposal.simulation_status,
            simulation_result=proposal.simulation_result,
            created_at=proposal.created_at,
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        ) from val_err


@router.post(
    "/{investigation_id}/remediation/execute-simulation",
    response_model=RemediationProposalResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Simulated Remediation",
    description="Execute an approved remediation action strictly inside the controlled simulation environment.",
)
def execute_simulated_remediation(
    investigation_id: str,
    session: Session = Depends(get_db),
) -> RemediationProposalResponse:
    """Execute remediation inside simulator."""
    try:
        proposal = investigation_service.execute_simulation(investigation_id, session)
        return RemediationProposalResponse(
            id=proposal.id,
            investigation_id=proposal.investigation_id,
            action_type=proposal.action_type,
            target_service=proposal.target_service,
            parameters=proposal.parameters,
            policy_decision=proposal.policy_decision,
            approval_status=proposal.approval_status,
            simulation_status=proposal.simulation_status,
            simulation_result=proposal.simulation_result,
            created_at=proposal.created_at,
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        ) from val_err
