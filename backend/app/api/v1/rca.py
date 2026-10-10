"""FastAPI endpoints for incident root-cause analysis, candidate ranking, and evidence queries."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.rca.schemas import (
    IncidentAnalysisListResponse,
    RootCauseAnalysisRequest,
    RootCauseAnalysisResponse,
)
from app.rca.service import RootCauseAnalysisService

logger = logging.getLogger("aegisops.rca.api")

router = APIRouter(prefix="/incidents/root-cause", tags=["Incident Root-Cause Analysis"])

rca_service = RootCauseAnalysisService()


@router.post(
    "/analyze",
    response_model=RootCauseAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze Incident Root Cause",
    description=(
        "Executes deterministic root-cause analysis over observed anomalies and trace error paths, "
        "ranking candidate initiating services with explainable multi-factor scoring."
    ),
)
def analyze_root_cause(
    payload: RootCauseAnalysisRequest,
    session: Session = Depends(get_db),
) -> RootCauseAnalysisResponse:
    """Run deterministic root-cause ranking over operational incident window."""
    try:
        return rca_service.analyze(request=payload, db=session)
    except Exception as exc:
        logger.exception("Root-cause analysis execution failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete root-cause analysis run.",
        ) from exc


@router.get(
    "/{analysis_id}",
    response_model=RootCauseAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Root-Cause Analysis Run",
    description="Retrieve a previously persisted root-cause analysis run by analysis ID.",
)
def get_root_cause_analysis(
    analysis_id: str,
    session: Session = Depends(get_db),
) -> RootCauseAnalysisResponse:
    """Retrieve persisted root-cause analysis report."""
    analysis = rca_service.get_analysis(analysis_id=analysis_id, db=session)
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Root-cause analysis run '{analysis_id}' not found.",
        )
    return analysis


@router.get(
    "",
    response_model=IncidentAnalysisListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Root-Cause Analysis Runs",
    description="Retrieve paginated list of historical root-cause analysis executions.",
)
def list_root_cause_analyses(
    limit: int = Query(default=20, ge=1, le=100, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> IncidentAnalysisListResponse:
    """List historical root-cause analysis runs."""
    return rca_service.list_analyses(limit=limit, offset=offset, db=session)
