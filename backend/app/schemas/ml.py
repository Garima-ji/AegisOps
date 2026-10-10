"""Typed Pydantic schemas for ML anomaly detection, model registry, and incident prediction."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnomalyDetectionRunRequest(BaseModel):
    """Payload to trigger ad-hoc or batch anomaly detection across configured detectors."""

    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=2, max_length=64, description="Target service to evaluate")
    window_end: datetime | None = Field(
        default=None, description="Observation cutoff timestamp (defaults to current UTC)"
    )
    window_seconds: float = Field(
        default=60.0,
        ge=10.0,
        le=3600.0,
        description="Window aggregation duration in seconds (10s to 1 hour)",
    )
    detector: Literal["all", "threshold", "rolling_mad", "isolation_forest"] = Field(
        default="all",
        description="Detector engine to execute ('all' runs baselines + Isolation Forest)",
    )
    persist_results: bool = Field(
        default=True,
        description="Whether to commit detection records to the database",
    )
    custom_features: dict[str, float] | None = Field(
        default=None,
        description="Optional precomputed feature vector to test directly without querying telemetry DB",
    )


class AnomalyDetectionResultSchema(BaseModel):
    """Detection output from an individual anomaly detector."""

    model_config = ConfigDict(extra="forbid")

    detector_name: str = Field(
        description="Name of the detector (e.g. threshold, rolling_mad, isolation_forest)"
    )
    is_anomaly: bool = Field(description="Boolean determination based on calibrated threshold")
    anomaly_score: float = Field(
        ge=0.0, le=1.0, description="Normalized anomaly score between 0.0 and 1.0"
    )
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        description="Operational severity classification"
    )
    triggering_features: list[str] = Field(
        default_factory=list, description="Top features contributing to the anomaly"
    )
    explanation: str = Field(description="Human-readable and machine-readable explanation")
    timestamp: datetime = Field(description="Evaluation timestamp")
    service_name: str = Field(description="Service evaluated")
    features: dict[str, float] = Field(
        default_factory=dict, description="Operational features evaluated"
    )


class AnomalyDetectionRunResponse(BaseModel):
    """Composite response from running anomaly detection."""

    model_config = ConfigDict(extra="forbid")

    service_name: str
    window_start: datetime
    window_end: datetime
    features: dict[str, float]
    detections: list[AnomalyDetectionResultSchema]


class AnomalyDetectionRecordSchema(BaseModel):
    """Persisted anomaly event record retrieved from database."""

    model_config = ConfigDict(extra="forbid")

    id: str
    service_name: str
    detector_name: str
    timestamp: datetime
    is_anomaly: bool
    anomaly_score: float
    severity: str
    triggering_features: list[str]
    explanation: str
    created_at: datetime


class AnomalyListResponse(BaseModel):
    """Paginated collection of anomaly detection records."""

    model_config = ConfigDict(extra="forbid")

    items: list[AnomalyDetectionRecordSchema]
    total: int
    limit: int
    offset: int


class MLModelResponse(BaseModel):
    """Registered model metadata, availability, and evaluation performance."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    model_version: str
    model_type: str
    status: Literal["AVAILABLE", "EXPERIMENTAL", "DEGRADED", "UNAVAILABLE"]
    last_trained_at: datetime | None
    artifact_path: str | None
    feature_schema_version: str
    evaluation_summary: dict[str, Any]
    created_at: datetime


class MLModelListResponse(BaseModel):
    """List of registered ML models."""

    model_config = ConfigDict(extra="forbid")

    models: list[MLModelResponse]
    total: int


class IncidentRiskPredictRequest(BaseModel):
    """Request payload to predict future incident occurrence within time horizon."""

    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(
        min_length=2, max_length=64, description="Target service to evaluate for risk"
    )
    prediction_timestamp: datetime | None = Field(
        default=None, description="Prediction timestamp cutoff (defaults to current UTC)"
    )
    horizon_seconds: float = Field(
        default=300.0,
        ge=30.0,
        le=1800.0,
        description="Forward-looking time horizon in seconds (default 300s = 5m)",
    )
    custom_features: dict[str, float] | None = Field(
        default=None,
        description="Optional feature vector to predict directly",
    )


class IncidentRiskPredictResponse(BaseModel):
    """Incident risk prediction inference output."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    model_version: str
    service_name: str
    prediction_timestamp: datetime
    horizon_seconds: float
    risk_probability: float = Field(
        ge=0.0, le=1.0, description="Estimated probability of incident within horizon"
    )
    predicted_incident: bool = Field(description="True if risk_probability exceeds model threshold")
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    contributing_factors: list[str]
    explanation: str
    features: dict[str, float]
    status: str
