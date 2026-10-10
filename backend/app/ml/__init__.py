"""Machine Learning package for AegisOps (Phase 3)."""

from app.ml.baselines import (
    AnomalyResult,
    RollingMadAnomalyDetector,
    ThresholdAnomalyDetector,
    compute_severity,
)
from app.ml.features import (
    STANDARD_FEATURE_NAMES,
    attach_incident_labels,
    build_sliding_feature_dataset,
    extract_features_from_db,
    extract_window_features,
)
from app.ml.incident_predictor import IncidentRiskPrediction, IncidentRiskPredictor
from app.ml.isolation_forest import IsolationForestDetector

__all__ = [
    "STANDARD_FEATURE_NAMES",
    "AnomalyResult",
    "IncidentRiskPrediction",
    "IncidentRiskPredictor",
    "IsolationForestDetector",
    "RollingMadAnomalyDetector",
    "ThresholdAnomalyDetector",
    "attach_incident_labels",
    "build_sliding_feature_dataset",
    "compute_severity",
    "extract_features_from_db",
    "extract_window_features",
]
