"""Deterministic statistical anomaly detection baselines with robust zero-variance handling."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass
class AnomalyResult:
    """Standardized detection result across all statistical and ML detectors."""

    detector_name: str
    is_anomaly: bool
    anomaly_score: float  # Normalized [0.0, 1.0]
    severity: str  # INFO, LOW, MEDIUM, HIGH, CRITICAL
    triggering_features: list[str]
    explanation: str
    timestamp: datetime
    service_name: str
    features: dict[str, float] = field(default_factory=dict)


def compute_severity(score: float) -> str:
    """Map normalized anomaly score to discrete severity classification."""
    if score >= 0.85:
        return "CRITICAL"
    if score >= 0.65:
        return "HIGH"
    if score >= 0.45:
        return "MEDIUM"
    if score >= 0.20:
        return "LOW"
    return "INFO"


class ThresholdAnomalyDetector:
    """Configurable threshold-based deterministic anomaly detector."""

    def __init__(
        self,
        error_rate_threshold: float = 0.10,
        latency_p95_threshold_ms: float = 500.0,
        freshness_lag_threshold_sec: float = 120.0,
        min_requests_for_error_rate: float = 5.0,
    ) -> None:
        self.error_rate_threshold = error_rate_threshold
        self.latency_p95_threshold_ms = latency_p95_threshold_ms
        self.freshness_lag_threshold_sec = freshness_lag_threshold_sec
        self.min_requests_for_error_rate = min_requests_for_error_rate

    def detect(
        self,
        features: dict[str, float],
        service_name: str,
        timestamp: datetime,
    ) -> AnomalyResult:
        """Evaluate threshold breaches against feature vector."""
        triggering: list[str] = []
        reasons: list[str] = []
        scores: list[float] = [0.0]

        # 1. Error Rate Rule
        err_rate = features.get("error_rate", 0.0)
        req_count = features.get("req_count", 0.0)
        if req_count >= self.min_requests_for_error_rate and err_rate > self.error_rate_threshold:
            triggering.append("error_rate")
            ratio = min(1.0, err_rate / max(1e-4, self.error_rate_threshold))
            scores.append(min(1.0, 0.5 + (0.5 * ratio)))
            reasons.append(
                f"Error rate ({err_rate:.1%}) exceeded threshold ({self.error_rate_threshold:.1%})"
            )

        # 2. Latency p95 Rule
        lat_p95 = features.get("latency_p95_ms", 0.0)
        if lat_p95 > self.latency_p95_threshold_ms:
            triggering.append("latency_p95_ms")
            ratio = min(2.0, lat_p95 / max(1.0, self.latency_p95_threshold_ms))
            scores.append(min(1.0, 0.4 + (0.3 * ratio)))
            reasons.append(
                f"P95 latency ({lat_p95:.1f}ms) breached limit ({self.latency_p95_threshold_ms:.1f}ms)"
            )

        # 3. Telemetry Freshness Lag Rule
        freshness = features.get("freshness_lag_seconds", 0.0)
        if freshness > self.freshness_lag_threshold_sec:
            triggering.append("freshness_lag_seconds")
            scores.append(0.75)
            reasons.append(
                f"Telemetry lag ({freshness:.1f}s) exceeded threshold ({self.freshness_lag_threshold_sec:.1f}s)"
            )

        max_score = round(float(max(scores)), 3)
        is_anomaly = len(triggering) > 0
        severity = compute_severity(max_score) if is_anomaly else "INFO"
        explanation = (
            "; ".join(reasons) if reasons else "All metrics within nominal operating thresholds."
        )

        return AnomalyResult(
            detector_name="threshold",
            is_anomaly=is_anomaly,
            anomaly_score=max_score,
            severity=severity,
            triggering_features=triggering,
            explanation=explanation,
            timestamp=timestamp,
            service_name=service_name,
            features=features,
        )


class RollingMadAnomalyDetector:
    """Rolling Median and Median Absolute Deviation (MAD) non-parametric anomaly detector.

    Uses Modified Z-score with explicit defense against zero variance and cold-start periods.
    """

    def __init__(
        self,
        z_threshold: float = 3.5,
        history_window: int = 30,
        min_history_size: int = 5,
        target_features: list[str] | None = None,
    ) -> None:
        self.z_threshold = z_threshold
        self.history_window = history_window
        self.min_history_size = min_history_size
        self.target_features = target_features or [
            "latency_p95_ms",
            "error_rate",
            "req_rate_per_sec",
            "cpu_utilization_mean",
        ]
        # In-memory history buffer per service: dict[service_name, dict[feature_name, deque[float]]]
        self._history: dict[str, dict[str, deque[float]]] = {}

    def update_history(self, service_name: str, features: dict[str, float]) -> None:
        """Append feature values to rolling history buffers."""
        if service_name not in self._history:
            self._history[service_name] = {
                feat: deque(maxlen=self.history_window) for feat in self.target_features
            }

        for feat in self.target_features:
            val = float(features.get(feat, 0.0))
            self._history[service_name][feat].append(val)

    def detect(
        self,
        features: dict[str, float],
        service_name: str,
        timestamp: datetime,
        update_after: bool = True,
    ) -> AnomalyResult:
        """Compute Modified Z-Score against historical rolling median and MAD."""
        triggering: list[str] = []
        reasons: list[str] = []
        max_z_ratio = 0.0

        svc_history = self._history.get(service_name, {})

        for feat in self.target_features:
            cur_val = float(features.get(feat, 0.0))
            feat_buf = svc_history.get(feat, deque())

            # Cold-start check
            if len(feat_buf) < self.min_history_size:
                continue

            arr = np.array(feat_buf, dtype=np.float64)
            median_val = float(np.median(arr))
            mad = float(np.median(np.abs(arr - median_val)))

            # Zero-variance guard: if MAD is 0 (constant stream), check discrete difference
            if mad < 1e-4:
                diff = abs(cur_val - median_val)
                # If metric jumped significantly from constant baseline
                if diff > 10.0 or (median_val == 0.0 and cur_val > 0.05):
                    triggering.append(feat)
                    reasons.append(
                        f"Metric '{feat}' deviated by {diff:.2f} from constant historical baseline ({median_val:.2f})"
                    )
                    max_z_ratio = max(max_z_ratio, 1.5)
                continue

            # Modified Z-Score formula: 0.6745 * (x - median) / MAD
            modified_z = 0.6745 * (cur_val - median_val) / mad
            abs_z = abs(modified_z)

            if abs_z > self.z_threshold:
                triggering.append(feat)
                ratio = abs_z / self.z_threshold
                max_z_ratio = max(max_z_ratio, ratio)
                reasons.append(
                    f"Metric '{feat}' Modified Z-score ({modified_z:.2f}) exceeded threshold ({self.z_threshold:.1f})"
                )

        if update_after:
            self.update_history(service_name, features)

        is_anomaly = len(triggering) > 0
        # Map z-score ratio to bounded [0.0, 1.0] score
        if is_anomaly:
            normalized_score = min(
                1.0, round(0.5 + (0.5 * (1.0 - (1.0 / max(1.0, max_z_ratio)))), 3)
            )
        else:
            normalized_score = 0.0

        severity = compute_severity(normalized_score) if is_anomaly else "INFO"
        explanation = (
            "; ".join(reasons) if reasons else "No anomalous deviation detected via rolling MAD."
        )

        return AnomalyResult(
            detector_name="rolling_mad",
            is_anomaly=is_anomaly,
            anomaly_score=normalized_score,
            severity=severity,
            triggering_features=triggering,
            explanation=explanation,
            timestamp=timestamp,
            service_name=service_name,
            features=features,
        )

    def reset(self) -> None:
        """Clear all historical state for testing isolation."""
        self._history.clear()
