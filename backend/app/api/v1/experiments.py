"""API endpoints for controlled fault-injection scenarios and ground-truth labels."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.experiment import (
    ExperimentCreateRequest,
    ExperimentResponse,
    FaultLabelListResponse,
    FaultLabelSchema,
)
from app.services.experiment_service import ExperimentService

router = APIRouter(prefix="/experiments", tags=["Experiments"])


@router.post(
    "",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Launch Controlled Fault-Injection Experiment",
    description="Registers and triggers a safe, bounded fault scenario against an allowlisted local service.",
)
def create_experiment(
    payload: ExperimentCreateRequest,
    session: Session = Depends(get_db),
) -> ExperimentResponse:
    """Create and start a controlled fault-injection experiment."""
    model = ExperimentService.create_experiment(session=session, req=payload)
    return ExperimentResponse(
        id=model.id,
        scenario_name=model.scenario_name,
        description=model.description,
        target_service=model.target_service,
        fault_type=model.fault_type,
        parameters=model.parameters or {},
        start_time=model.start_time,
        end_time=model.end_time,
        status=model.status,  # type: ignore[arg-type]
        initiating_service=model.initiating_service,
        symptom_services=model.symptom_services or [],
        cleanup_result=model.cleanup_result,
        created_at=model.created_at,
    )


@router.get(
    "",
    response_model=list[ExperimentResponse],
    status_code=status.HTTP_200_OK,
    summary="List Experiments",
    description="List active and historical fault-injection experiments with status metadata.",
)
def list_experiments(
    limit: int = Query(default=50, ge=1, le=200, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> list[ExperimentResponse]:
    """Retrieve historical and active experiments."""
    models, _ = ExperimentService.list_experiments(session=session, limit=limit, offset=offset)
    return [
        ExperimentResponse(
            id=m.id,
            scenario_name=m.scenario_name,
            description=m.description,
            target_service=m.target_service,
            fault_type=m.fault_type,
            parameters=m.parameters or {},
            start_time=m.start_time,
            end_time=m.end_time,
            status=m.status,  # type: ignore[arg-type]
            initiating_service=m.initiating_service,
            symptom_services=m.symptom_services or [],
            cleanup_result=m.cleanup_result,
            created_at=m.created_at,
        )
        for m in models
    ]


@router.get(
    "/{experiment_id}",
    response_model=ExperimentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Experiment Status",
    description="Retrieve execution state, timestamps, and parameters for an experiment.",
)
def get_experiment(
    experiment_id: str,
    session: Session = Depends(get_db),
) -> ExperimentResponse:
    """Retrieve a single experiment by ID."""
    model = ExperimentService.get_experiment(session=session, experiment_id=experiment_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiment '{experiment_id}' not found.",
        )
    return ExperimentResponse(
        id=model.id,
        scenario_name=model.scenario_name,
        description=model.description,
        target_service=model.target_service,
        fault_type=model.fault_type,
        parameters=model.parameters or {},
        start_time=model.start_time,
        end_time=model.end_time,
        status=model.status,  # type: ignore[arg-type]
        initiating_service=model.initiating_service,
        symptom_services=model.symptom_services or [],
        cleanup_result=model.cleanup_result,
        created_at=model.created_at,
    )


@router.get(
    "/{experiment_id}/labels",
    response_model=FaultLabelListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Experiment Ground-Truth Labels",
    description="Retrieve ground-truth labels for ML model evaluation and benchmark validation.",
)
def get_experiment_labels(
    experiment_id: str,
    limit: int = Query(default=500, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> FaultLabelListResponse:
    """Fetch ground-truth labels for a given experiment."""
    models, total = ExperimentService.get_experiment_labels(
        session=session, experiment_id=experiment_id, limit=limit, offset=offset
    )
    labels = [
        FaultLabelSchema(
            id=m.id,
            experiment_id=m.experiment_id,
            timestamp=m.timestamp,
            service_name=m.service_name,
            is_fault=m.is_fault,
            fault_type=m.fault_type,
            severity=m.severity,
            metadata=m.metadata_json or {},
        )
        for m in models
    ]
    return FaultLabelListResponse(experiment_id=experiment_id, labels=labels, total=total)


@router.post(
    "/{experiment_id}/stop",
    response_model=ExperimentResponse,
    status_code=status.HTTP_200_OK,
    summary="Safely Stop Experiment",
    description="Interrupts an active experiment early and marks cleanup completed.",
)
def stop_experiment(
    experiment_id: str,
    session: Session = Depends(get_db),
) -> ExperimentResponse:
    """Safely terminate an active experiment."""
    model = ExperimentService.stop_experiment(session=session, experiment_id=experiment_id)
    return ExperimentResponse(
        id=model.id,
        scenario_name=model.scenario_name,
        description=model.description,
        target_service=model.target_service,
        fault_type=model.fault_type,
        parameters=model.parameters or {},
        start_time=model.start_time,
        end_time=model.end_time,
        status=model.status,  # type: ignore[arg-type]
        initiating_service=model.initiating_service,
        symptom_services=model.symptom_services or [],
        cleanup_result=model.cleanup_result,
        created_at=model.created_at,
    )
