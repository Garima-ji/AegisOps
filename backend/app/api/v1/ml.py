"""FastAPI endpoints for ML anomaly detection, model registry, and incident prediction."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ml import (
    AnomalyDetectionRunRequest,
    AnomalyDetectionRunResponse,
    AnomalyListResponse,
    IncidentRiskPredictRequest,
    IncidentRiskPredictResponse,
    MLModelListResponse,
    MLModelResponse,
)
from app.services.ml_service import ml_service

router = APIRouter(prefix="/ml", tags=["Machine Learning"])


@router.post(
    "/anomaly-detection/run",
    response_model=AnomalyDetectionRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Run Anomaly Detection",
    description="Extracts windowed operational features and evaluates them against statistical and ML anomaly detectors.",
)
def run_anomaly_detection(
    payload: AnomalyDetectionRunRequest,
    session: Session = Depends(get_db),
) -> AnomalyDetectionRunResponse:
    """Execute anomaly detection pipeline across requested detectors."""
    return ml_service.run_anomaly_detection(db=session, req=payload)


@router.get(
    "/anomalies",
    response_model=AnomalyListResponse,
    status_code=status.HTTP_200_OK,
    summary="Query Persisted Anomalies",
    description="Retrieve paginated anomaly detection events with optional service, detector, and severity filtering.",
)
def list_anomalies(
    service_name: str | None = Query(default=None, description="Filter by service name"),
    detector_name: str | None = Query(default=None, description="Filter by detector name"),
    severity: str | None = Query(
        default=None, description="Filter by severity (INFO, LOW, MEDIUM, HIGH, CRITICAL)"
    ),
    is_anomaly: bool | None = Query(default=None, description="Filter by anomaly flag"),
    start_time: datetime | None = Query(default=None, description="Start timestamp"),
    end_time: datetime | None = Query(default=None, description="End timestamp"),
    limit: int = Query(default=100, ge=1, le=500, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    session: Session = Depends(get_db),
) -> AnomalyListResponse:
    """List persisted anomaly detection records."""
    return ml_service.list_anomalies(
        db=session,
        service_name=service_name,
        detector_name=detector_name,
        severity=severity,
        is_anomaly=is_anomaly,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/models",
    response_model=MLModelListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Registered ML Models",
    description="List all registered anomaly detection and incident risk models, their training state, and evaluation metrics.",
)
def list_models(
    session: Session = Depends(get_db),
) -> MLModelListResponse:
    """Retrieve all models from the ML model registry."""
    return ml_service.list_models(db=session)


@router.get(
    "/models/{model_name}",
    response_model=MLModelResponse,
    status_code=status.HTTP_200_OK,
    summary="Get ML Model Metadata",
    description="Retrieve registration, version, artifact path, and evaluation summary for a specific model.",
)
def get_model(
    model_name: str,
    session: Session = Depends(get_db),
) -> MLModelResponse:
    """Retrieve metadata for a specific model."""
    return ml_service.get_model(db=session, model_name=model_name)


@router.post(
    "/incident-risk/predict",
    response_model=IncidentRiskPredictResponse,
    status_code=status.HTTP_200_OK,
    summary="Predict Incident Risk",
    description="Estimates the probability that a service will experience an incident within a forward-looking time horizon.",
)
def predict_incident_risk(
    payload: IncidentRiskPredictRequest,
    session: Session = Depends(get_db),
) -> IncidentRiskPredictResponse:
    """Inference endpoint for forward-looking incident risk prediction."""
    return ml_service.predict_incident_risk(db=session, req=payload)
