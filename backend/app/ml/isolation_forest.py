"""Isolation Forest unsupervised anomaly detector with schema validation and calibrated scoring."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.ml.baselines import AnomalyResult, compute_severity
from app.ml.features import STANDARD_FEATURE_NAMES


class IsolationForestDetector:
    """Production-grade Isolation Forest anomaly detector.

    Features:
    - Fixed random state for reproducibility.
    - Fitted preprocessing pipeline (median imputation + standard scaling).
    - Normalized anomaly score in [0.0, 1.0], where 1.0 represents extreme abnormality.
    - Strict feature schema validation and ordering.
    - Top triggering feature attribution via scaled deviation.
    - Model persistence and metadata serialization.
    """

    def __init__(
        self,
        model_version: str = "v1.0.0",
        contamination: float = 0.05,
        random_state: int = 42,
        alert_threshold: float = 0.60,
        feature_names: list[str] | None = None,
    ) -> None:
        self.model_name = "isolation_forest"
        self.model_version = model_version
        self.contamination = contamination
        self.random_state = random_state
        self.alert_threshold = alert_threshold
        self.feature_names = feature_names or list(STANDARD_FEATURE_NAMES)

        self._pipeline: Pipeline | None = None
        self._score_min: float = -0.5
        self._score_max: float = 0.5
        self.metadata: dict[str, Any] = {}
        self.is_fitted: bool = False

    def fit(
        self,
        df: pd.DataFrame,
        training_metadata: dict[str, Any] | None = None,
    ) -> "IsolationForestDetector":
        """Fit preprocessing pipeline and Isolation Forest on normal/representative operational data."""
        X = self._validate_and_extract_matrix(df)

        imputer = SimpleImputer(strategy="median")
        scaler = StandardScaler()
        iso = IsolationForest(
            contamination=self.contamination,
            random_state=self.random_state,
            n_estimators=100,
            n_jobs=-1,
        )

        pipeline = Pipeline(
            [
                ("imputer", imputer),
                ("scaler", scaler),
                ("isolation_forest", iso),
            ]
        )

        pipeline.fit(X)
        self._pipeline = pipeline
        self.is_fitted = True

        # Compute calibration range from training decision scores
        raw_scores = iso.decision_function(scaler.transform(imputer.transform(X)))
        self._score_min = float(np.percentile(raw_scores, 1))
        self._score_max = float(np.percentile(raw_scores, 99))
        if self._score_max <= self._score_min:
            self._score_max = self._score_min + 1.0

        self.metadata = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "fitted_at": datetime.now(UTC).isoformat(),
            "n_samples": int(len(df)),
            "n_features": len(self.feature_names),
            "feature_names": self.feature_names,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "alert_threshold": self.alert_threshold,
            "score_min_calibrated": self._score_min,
            "score_max_calibrated": self._score_max,
            **(training_metadata or {}),
        }
        return self

    def _validate_and_extract_matrix(self, data: pd.DataFrame | dict[str, float]) -> np.ndarray:
        """Validate input schema and align columns to exact standard feature order."""
        if isinstance(data, dict):
            row = [data.get(feat, np.nan) for feat in self.feature_names]
            return np.array([row], dtype=float)

        if isinstance(data, pd.DataFrame):
            for feat in self.feature_names:
                if feat not in data.columns:
                    raise ValueError(
                        f"Missing required feature '{feat}' for Isolation Forest inference. "
                        f"Expected schema: {self.feature_names}"
                    )
            return np.asarray(data[self.feature_names].to_numpy(dtype=float))

        raise TypeError(f"Expected DataFrame or dict, got {type(data)}")

    def score_features(self, features: dict[str, float] | pd.DataFrame) -> np.ndarray:
        """Compute normalized anomaly score S in [0.0, 1.0], where higher = more anomalous."""
        if not self.is_fitted or self._pipeline is None:
            raise RuntimeError(
                "IsolationForestDetector is not fitted. Train or load weights first."
            )

        X = self._validate_and_extract_matrix(features)
        imputed = self._pipeline.named_steps["imputer"].transform(X)
        scaled = self._pipeline.named_steps["scaler"].transform(imputed)
        iso: IsolationForest = self._pipeline.named_steps["isolation_forest"]

        # decision_function: large positive = normal, negative = anomaly
        raw_scores = iso.decision_function(scaled)

        # Invert and normalize so that lower raw score -> higher anomaly score
        normalized = (self._score_max - raw_scores) / max(1e-6, self._score_max - self._score_min)
        scores = np.clip(normalized, 0.0, 1.0)
        return np.asarray(scores)

    def detect(
        self,
        features: dict[str, float],
        service_name: str,
        timestamp: datetime,
    ) -> AnomalyResult:
        """Run single-sample detection and produce structured AnomalyResult."""
        if not self.is_fitted or self._pipeline is None:
            raise RuntimeError("IsolationForestDetector is not fitted.")

        score = float(self.score_features(features)[0])
        score = round(score, 3)
        is_anomaly = score >= self.alert_threshold

        # Feature attribution: find top 3 features with highest absolute scaled deviation
        X = self._validate_and_extract_matrix(features)
        imputed = self._pipeline.named_steps["imputer"].transform(X)
        scaled = self._pipeline.named_steps["scaler"].transform(imputed)[0]

        abs_scaled = np.abs(scaled)
        top_indices = np.argsort(abs_scaled)[::-1][:3]
        triggering = [self.feature_names[idx] for idx in top_indices if abs_scaled[idx] >= 1.5]

        if is_anomaly and not triggering:
            # Multi-variate subspace outlier even if individual metrics haven't spiked 1.5 standard deviations
            triggering = [self.feature_names[idx] for idx in top_indices[:2]]

        severity = compute_severity(score) if is_anomaly else "INFO"

        if is_anomaly:
            attr_str = ", ".join([f"{f}={features.get(f, 0.0):.2f}" for f in triggering])
            explanation = (
                f"Isolation Forest identified multivariate anomaly (score={score:.3f} >= {self.alert_threshold}). "
                f"Key contributing deviations: {attr_str}"
            )
        else:
            explanation = f"Operational metrics nominal. Isolation Forest anomaly score={score:.3f} < {self.alert_threshold}."

        return AnomalyResult(
            detector_name=self.model_name,
            is_anomaly=is_anomaly,
            anomaly_score=score,
            severity=severity,
            triggering_features=triggering,
            explanation=explanation,
            timestamp=timestamp,
            service_name=service_name,
            features=features,
        )

    def save(self, filepath: str | Path) -> None:
        """Persist model pipeline and calibration metadata to disk."""
        if not self.is_fitted or self._pipeline is None:
            raise RuntimeError("Cannot save unfitted IsolationForestDetector.")

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "alert_threshold": self.alert_threshold,
            "feature_names": self.feature_names,
            "pipeline": self._pipeline,
            "score_min": self._score_min,
            "score_max": self._score_max,
            "metadata": self.metadata,
        }
        joblib.dump(payload, path)

    @classmethod
    def load(cls, filepath: str | Path) -> "IsolationForestDetector":
        """Load persisted IsolationForestDetector from disk."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Model artifact not found at {path}")

        payload: dict[str, Any] = joblib.load(path)
        detector = cls(
            model_version=payload.get("model_version", "v1.0.0"),
            contamination=payload.get("contamination", 0.05),
            random_state=payload.get("random_state", 42),
            alert_threshold=payload.get("alert_threshold", 0.60),
            feature_names=payload.get("feature_names", list(STANDARD_FEATURE_NAMES)),
        )
        detector._pipeline = payload["pipeline"]
        detector._score_min = payload.get("score_min", -0.5)
        detector._score_max = payload.get("score_max", 0.5)
        detector.metadata = payload.get("metadata", {})
        detector.is_fitted = True
        return detector
