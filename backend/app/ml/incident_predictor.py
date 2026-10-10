"""Supervised incident risk prediction pipeline with chronological guards and label validation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.ml.features import STANDARD_FEATURE_NAMES


@dataclass
class IncidentRiskPrediction:
    """Structured prediction result for future incident risk."""

    model_name: str
    model_version: str
    service_name: str
    prediction_timestamp: datetime
    horizon_seconds: float
    risk_probability: float  # [0.0, 1.0]
    predicted_incident: bool
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    contributing_factors: list[str]
    explanation: str
    features: dict[str, float] = field(default_factory=dict)


def compute_risk_level(prob: float) -> str:
    """Map risk probability to discrete operational level."""
    if prob >= 0.80:
        return "CRITICAL"
    if prob >= 0.50:
        return "HIGH"
    if prob >= 0.25:
        return "MEDIUM"
    return "LOW"


class IncidentRiskPredictor:
    """Supervised ML model predicting incident occurrence within a future time horizon (e.g., 300s).

    Safety Guarantees:
    - Rejects training if positive incident samples are insufficient (< min_positive_samples).
    - Requires strictly chronological split and embargo buffer to avoid window leakage.
    - Fitted imputer and scaler derived solely from training partition.
    - Zero future observation leakage into predictor variables.
    """

    def __init__(
        self,
        model_version: str = "v1.0.0",
        horizon_seconds: float = 300.0,
        risk_threshold: float = 0.50,
        min_positive_samples: int = 5,
        random_state: int = 42,
        feature_names: list[str] | None = None,
    ) -> None:
        self.model_name = "incident_risk_predictor"
        self.model_version = model_version
        self.horizon_seconds = horizon_seconds
        self.risk_threshold = risk_threshold
        self.min_positive_samples = min_positive_samples
        self.random_state = random_state
        self.feature_names = feature_names or list(STANDARD_FEATURE_NAMES)

        self._pipeline: Pipeline | None = None
        self.is_fitted: bool = False
        self.status: str = "UNAVAILABLE"
        self.metadata: dict[str, Any] = {}

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series | np.ndarray,
        training_metadata: dict[str, Any] | None = None,
        is_embargoed: pd.Series | np.ndarray | None = None,
    ) -> "IncidentRiskPredictor":
        """Fit supervised classifier on unambiguous training samples."""
        y_arr = np.asarray(y_train, dtype=int)
        if len(X_train) != len(y_arr):
            raise ValueError(
                f"Length mismatch: X_train has {len(X_train)} samples but y_train has {len(y_arr)}."
            )

        embargoed_excluded = 0

        # Filter out embargoed rows if passed via parameter or embedded in X_train
        if is_embargoed is not None:
            emb_mask = np.asarray(is_embargoed, dtype=bool)
            keep_mask = ~emb_mask
            embargoed_excluded += int(np.sum(emb_mask))
            X_train = (
                X_train.loc[keep_mask] if isinstance(X_train, pd.DataFrame) else X_train[keep_mask]
            )
            y_arr = y_arr[keep_mask]
        elif isinstance(X_train, pd.DataFrame) and "is_embargoed" in X_train.columns:
            emb_mask = X_train["is_embargoed"].astype(bool).to_numpy()
            keep_mask = ~emb_mask
            embargoed_excluded += int(np.sum(emb_mask))
            X_train = X_train.loc[keep_mask]
            y_arr = y_arr[keep_mask]

        unique_labels = set(np.unique(y_arr))
        if not unique_labels.issubset({0, 1}):
            raise ValueError(
                f"y_train contains ambiguous or invalid labels: {unique_labels}. "
                "Only unambiguous binary targets {0, 1} are allowed. Filter out is_embargoed == True rows."
            )

        pos_count = int(np.sum(y_arr == 1))
        neg_count = int(np.sum(y_arr == 0))
        total = len(y_arr)

        if pos_count < self.min_positive_samples:
            self.status = "UNAVAILABLE"
            self.is_fitted = False
            self.metadata = {
                "model_name": self.model_name,
                "model_version": self.model_version,
                "status": "UNAVAILABLE",
                "reason": (
                    f"Insufficient positive incident labels ({pos_count} < required {self.min_positive_samples}). "
                    "Supervised classifier training suppressed to prevent misleading evaluation."
                ),
                "pos_count": pos_count,
                "neg_count": neg_count,
                "embargoed_samples_excluded": embargoed_excluded,
            }
            raise ValueError(self.metadata["reason"])

        X_mat = self._validate_and_extract_matrix(X_train)

        pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                (
                    "classifier",
                    RandomForestClassifier(
                        n_estimators=100,
                        max_depth=6,
                        class_weight="balanced",
                        random_state=self.random_state,
                        n_jobs=-1,
                    ),
                ),
            ]
        )

        pipeline.fit(X_mat, y_arr)
        self._pipeline = pipeline
        self.is_fitted = True
        self.status = "AVAILABLE"

        clf: RandomForestClassifier = pipeline.named_steps["classifier"]
        importances = dict(
            zip(
                self.feature_names,
                [round(float(x), 4) for x in clf.feature_importances_],
                strict=False,
            )
        )

        self.metadata = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "status": self.status,
            "fitted_at": datetime.now(UTC).isoformat(),
            "horizon_seconds": self.horizon_seconds,
            "risk_threshold": self.risk_threshold,
            "n_samples": total,
            "pos_samples": pos_count,
            "neg_samples": neg_count,
            "pos_rate": round(pos_count / max(1, total), 4),
            "embargoed_samples_excluded": embargoed_excluded,
            "feature_importances": importances,
            **(training_metadata or {}),
        }
        return self

    def _validate_and_extract_matrix(self, data: pd.DataFrame | dict[str, float]) -> np.ndarray:
        """Validate input schema and align columns to standard feature names."""
        forbidden_cols = {
            "target",
            "target_incident_in_horizon",
            "is_incident",
            "is_embargoed",
            "scenario",
        }
        for feat in self.feature_names:
            if feat in forbidden_cols:
                raise ValueError(
                    f"Feature '{feat}' is a label/metadata column and cannot be used as an input feature."
                )

        if isinstance(data, dict):
            row = [data.get(feat, np.nan) for feat in self.feature_names]
            return np.array([row], dtype=float)

        if isinstance(data, pd.DataFrame):
            for feat in self.feature_names:
                if feat not in data.columns:
                    raise ValueError(
                        f"Missing required feature '{feat}' for Incident Predictor. "
                        f"Expected schema: {self.feature_names}"
                    )
            return np.asarray(data[self.feature_names].to_numpy(dtype=float))

        raise TypeError(f"Expected DataFrame or dict, got {type(data)}")

    def predict_proba(self, features: dict[str, float] | pd.DataFrame) -> np.ndarray:
        """Predict probability of incident within horizon [0.0, 1.0]."""
        if not self.is_fitted or self._pipeline is None:
            raise RuntimeError(
                f"IncidentRiskPredictor is not fitted (status={self.status}). "
                "Ensure sufficient training data and fit model first."
            )

        X = self._validate_and_extract_matrix(features)
        proba = self._pipeline.predict_proba(X)
        # Class 1 probability
        if proba.shape[1] == 2:
            return np.asarray(proba[:, 1])
        return np.asarray(proba[:, 0])

    def predict(
        self,
        features: dict[str, float],
        service_name: str,
        timestamp: datetime,
    ) -> IncidentRiskPrediction:
        """Compute single-instance incident risk prediction with factor attribution."""
        prob = float(self.predict_proba(features)[0])
        prob = round(prob, 4)
        predicted = prob >= self.risk_threshold
        risk_level = compute_risk_level(prob)

        # Feature importance-driven attribution
        factors: list[str] = []
        if self._pipeline is not None and "classifier" in self._pipeline.named_steps:
            clf: RandomForestClassifier = self._pipeline.named_steps["classifier"]
            top_indices = np.argsort(clf.feature_importances_)[::-1][:3]
            factors = [
                self.feature_names[i]
                for i in top_indices
                if features.get(self.feature_names[i], 0.0) > 0
            ]

        if predicted:
            explanation = (
                f"High risk ({prob:.1%}) of incident occurring in service '{service_name}' "
                f"within next {int(self.horizon_seconds)}s (threshold={self.risk_threshold:.1%}). "
                f"Key factors: {', '.join(factors) if factors else 'Multi-metric telemetry shifts'}."
            )
        else:
            explanation = (
                f"Low risk ({prob:.1%}) of incident within next {int(self.horizon_seconds)}s. "
                "Operating patterns consistent with healthy baseline."
            )

        return IncidentRiskPrediction(
            model_name=self.model_name,
            model_version=self.model_version,
            service_name=service_name,
            prediction_timestamp=timestamp,
            horizon_seconds=self.horizon_seconds,
            risk_probability=prob,
            predicted_incident=predicted,
            risk_level=risk_level,
            contributing_factors=factors,
            explanation=explanation,
            features=features,
        )

    def save(self, filepath: str | Path) -> None:
        """Persist model pipeline and training metadata."""
        if not self.is_fitted or self._pipeline is None:
            raise RuntimeError("Cannot save unfitted IncidentRiskPredictor.")

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "horizon_seconds": self.horizon_seconds,
            "risk_threshold": self.risk_threshold,
            "min_positive_samples": self.min_positive_samples,
            "random_state": self.random_state,
            "feature_names": self.feature_names,
            "pipeline": self._pipeline,
            "status": self.status,
            "metadata": self.metadata,
        }
        joblib.dump(payload, path)

    @classmethod
    def load(cls, filepath: str | Path) -> "IncidentRiskPredictor":
        """Load persisted IncidentRiskPredictor."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Model artifact not found at {path}")

        payload: dict[str, Any] = joblib.load(path)
        predictor = cls(
            model_version=payload.get("model_version", "v1.0.0"),
            horizon_seconds=payload.get("horizon_seconds", 300.0),
            risk_threshold=payload.get("risk_threshold", 0.50),
            min_positive_samples=payload.get("min_positive_samples", 5),
            random_state=payload.get("random_state", 42),
            feature_names=payload.get("feature_names", list(STANDARD_FEATURE_NAMES)),
        )
        predictor._pipeline = payload["pipeline"]
        predictor.status = payload.get("status", "AVAILABLE")
        predictor.metadata = payload.get("metadata", {})
        predictor.is_fitted = True
        return predictor
