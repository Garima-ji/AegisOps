"""Data quality, feature drift, and prediction distribution monitoring for AegisOps ML."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import numpy as np

from app.ml.features import STANDARD_FEATURE_NAMES
from app.observability.metrics import increment_counter

logger = logging.getLogger("aegisops.ml.drift")


class DriftCategory(StrEnum):
    """Explicitly distinguished categories of distribution and performance variation."""

    NO_DRIFT = "NO_DRIFT"
    DATA_QUALITY_ISSUE = "DATA_QUALITY_ISSUE"  # Malformed, missing, or out-of-range telemetry
    INPUT_FEATURE_DRIFT = (
        "INPUT_FEATURE_DRIFT"  # Statistical shift in input distributions (e.g. PSI > 0.25)
    )
    PREDICTION_DRIFT = "PREDICTION_DRIFT"  # Shift in model output or alert frequency
    CONFIRMED_PERFORMANCE_DEGRADATION = (
        "CONFIRMED_PERFORMANCE_DEGRADATION"  # Verified label accuracy decline
    )


class DriftSeverity(StrEnum):
    """Severity of observed drift."""

    NONE = "NONE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class FeatureDriftMetric:
    """Statistical drift comparison for a single feature."""

    feature_name: str
    baseline_mean: float
    current_mean: float
    baseline_std: float
    current_std: float
    psi_score: float
    drift_severity: DriftSeverity
    details: str


@dataclass
class DataQualityCheck:
    """Individual data quality rule validation."""

    rule_name: str
    feature_or_field: str
    passed: bool
    observed_value: Any
    expected_constraint: str
    message: str


@dataclass
class DriftReport:
    """Comprehensive snapshot of telemetry data quality, input drift, and prediction stability."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    sample_count: int = 0
    overall_category: DriftCategory = DriftCategory.NO_DRIFT
    data_quality_passed: bool = True
    input_drift_detected: bool = False
    prediction_drift_detected: bool = False
    quality_checks: list[DataQualityCheck] = field(default_factory=list)
    feature_metrics: list[FeatureDriftMetric] = field(default_factory=list)
    prediction_shift_summary: dict[str, Any] = field(default_factory=dict)
    summary_message: str = "All monitored distributions and quality rules are healthy."

    def to_dict(self) -> dict[str, Any]:
        """Serialize drift report to dictionary."""
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


def calculate_psi(
    expected: np.ndarray | list[float],
    actual: np.ndarray | list[float],
    num_buckets: int = 5,
) -> float:
    """Calculate Population Stability Index (PSI) between baseline and observed distributions.

    PSI = sum( (Actual% - Expected%) * ln(Actual% / Expected%) )
    Interpretations:
      PSI < 0.10: No significant change
      0.10 <= PSI < 0.25: Moderate change / warning
      PSI >= 0.25: Significant shift / critical drift
    """
    exp_arr = np.asarray(expected, dtype=float)
    act_arr = np.asarray(actual, dtype=float)

    if len(exp_arr) < 2 or len(act_arr) < 2:
        return 0.0

    # Handle zero-variance cases
    if np.all(exp_arr == exp_arr[0]) and np.all(act_arr == act_arr[0]):
        return 0.0 if exp_arr[0] == act_arr[0] else 1.0

    # Define quantile buckets on expected distribution
    try:
        quantiles = np.linspace(0, 100, num_buckets + 1)
        bucket_bounds = np.percentile(exp_arr, quantiles)
        # Deduplicate bounds if distribution is discrete
        bucket_bounds = np.unique(bucket_bounds)
        if len(bucket_bounds) < 2:
            return 0.0

        bucket_bounds[0] = -np.inf
        bucket_bounds[-1] = np.inf

        # Calculate counts per bin
        exp_counts, _ = np.histogram(exp_arr, bins=bucket_bounds)
        act_counts, _ = np.histogram(act_arr, bins=bucket_bounds)

        # Laplace smoothing (add-1) to avoid zero-probability divergence
        exp_pct = (exp_counts + 1.0) / (np.sum(exp_counts) + len(exp_counts))
        act_pct = (act_counts + 1.0) / (np.sum(act_counts) + len(act_counts))

        psi_val = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
        return float(max(0.0, psi_val))
    except Exception as exc:
        logger.warning("Error calculating PSI: %s", exc)
        return 0.0


class DriftMonitor:
    """Online monitor tracking feature distribution shifts, data quality, and prediction rates."""

    def __init__(
        self,
        baseline_stats: dict[str, dict[str, float]] | None = None,
        psi_warning_threshold: float = 0.10,
        psi_critical_threshold: float = 0.25,
    ) -> None:
        self.psi_warning = psi_warning_threshold
        self.psi_critical = psi_critical_threshold
        # Default standard baseline expectations based on trained model distributions
        self.baseline_stats = baseline_stats or self._default_baseline_stats()

    @staticmethod
    def _default_baseline_stats() -> dict[str, dict[str, float]]:
        """Construct reasonable baseline reference statistics for standard features."""
        stats: dict[str, dict[str, float]] = {}
        for feat in STANDARD_FEATURE_NAMES:
            if "utilization" in feat:
                stats[feat] = {"mean": 0.40, "std": 0.15, "min": 0.0, "max": 1.0}
            elif "latency" in feat:
                stats[feat] = {"mean": 45.0, "std": 20.0, "min": 0.0, "max": 10000.0}
            elif "error" in feat:
                stats[feat] = {"mean": 0.02, "std": 0.05, "min": 0.0, "max": 1.0}
            elif "missingness" in feat:
                stats[feat] = {"mean": 0.01, "std": 0.02, "min": 0.0, "max": 1.0}
            elif "lag" in feat:
                stats[feat] = {"mean": 5.0, "std": 3.0, "min": 0.0, "max": 300.0}
            else:
                stats[feat] = {"mean": 50.0, "std": 25.0, "min": 0.0, "max": 100000.0}
        return stats

    def check_telemetry_quality(
        self, feature_records: list[dict[str, float]]
    ) -> list[DataQualityCheck]:
        """Perform deterministic schema, range, and staleness validation on incoming feature rows."""
        checks: list[DataQualityCheck] = []
        if not feature_records:
            checks.append(
                DataQualityCheck(
                    rule_name="empty_payload",
                    feature_or_field="all",
                    passed=False,
                    observed_value=0,
                    expected_constraint="> 0 rows",
                    message="Telemetry sample contains zero feature observations.",
                )
            )
            return checks

        # Check required schema coverage
        for feat in STANDARD_FEATURE_NAMES:
            missing_count = sum(1 for r in feature_records if feat not in r or r[feat] is None)
            missing_ratio = missing_count / len(feature_records)
            passed = missing_ratio <= 0.10
            checks.append(
                DataQualityCheck(
                    rule_name="schema_completeness",
                    feature_or_field=feat,
                    passed=passed,
                    observed_value=f"{missing_ratio:.2%}",
                    expected_constraint="<= 10.0% missing",
                    message=f"Missing ratio for feature '{feat}' is {missing_ratio:.2%}.",
                )
            )

        # Check domain range bounds
        for row in feature_records:
            cpu = row.get("cpu_utilization_mean", 0.0)
            mem = row.get("memory_utilization_mean", 0.0)
            latency = row.get("latency_mean_ms", 0.0)
            lag = row.get("freshness_lag_seconds", 0.0)

            if not (0.0 <= cpu <= 1.0):
                checks.append(
                    DataQualityCheck(
                        rule_name="value_range",
                        feature_or_field="cpu_utilization_mean",
                        passed=False,
                        observed_value=cpu,
                        expected_constraint="[0.0, 1.0]",
                        message=f"Out of bounds CPU utilization observed: {cpu}",
                    )
                )
            if not (0.0 <= mem <= 1.0):
                checks.append(
                    DataQualityCheck(
                        rule_name="value_range",
                        feature_or_field="memory_utilization_mean",
                        passed=False,
                        observed_value=mem,
                        expected_constraint="[0.0, 1.0]",
                        message=f"Out of bounds memory utilization observed: {mem}",
                    )
                )
            if latency < 0.0:
                checks.append(
                    DataQualityCheck(
                        rule_name="value_range",
                        feature_or_field="latency_mean_ms",
                        passed=False,
                        observed_value=latency,
                        expected_constraint=">= 0.0",
                        message=f"Negative latency measured: {latency}",
                    )
                )
            if lag > 300.0:
                checks.append(
                    DataQualityCheck(
                        rule_name="freshness_lag",
                        feature_or_field="freshness_lag_seconds",
                        passed=False,
                        observed_value=lag,
                        expected_constraint="<= 300.0s",
                        message=f"Stale telemetry detected with freshness lag {lag:.1f}s",
                    )
                )
        return checks

    def evaluate_drift(
        self,
        current_features: list[dict[str, float]],
        recent_predictions: list[float] | None = None,
        baseline_prediction_alert_rate: float = 0.05,
    ) -> DriftReport:
        """Run full evaluation across data quality, input feature drift, and prediction distribution."""
        report = DriftReport(sample_count=len(current_features))

        # 1. Data Quality Checks
        quality_checks = self.check_telemetry_quality(current_features)
        report.quality_checks = quality_checks
        failed_quality = [c for c in quality_checks if not c.passed]
        report.data_quality_passed = len(failed_quality) == 0

        if not report.data_quality_passed:
            report.overall_category = DriftCategory.DATA_QUALITY_ISSUE
            report.summary_message = (
                f"Data quality checks failed: {len(failed_quality)} rule violations detected."
            )
            increment_counter("telemetry.quality.schema_deviations_total", len(failed_quality))
            return report

        # 2. Feature Distribution Drift (PSI & Z-score)
        feature_metrics: list[FeatureDriftMetric] = []

        for feat in STANDARD_FEATURE_NAMES:
            values = [r[feat] for r in current_features if feat in r and r[feat] is not None]
            if len(values) < 5:
                continue

            cur_mean = float(np.mean(values))
            cur_std = float(np.std(values))
            b_info = self.baseline_stats.get(feat, {"mean": cur_mean, "std": max(cur_std, 1e-4)})
            b_mean = b_info["mean"]
            b_std = max(b_info.get("std", 1.0), 1e-4)

            # Generate deterministic baseline reference distribution matching known mean & std
            rng = np.random.default_rng(seed=abs(hash(feat)) % (2**31))
            sim_baseline = rng.normal(b_mean, b_std, size=200)
            if "min" in b_info and "max" in b_info:
                sim_baseline = np.clip(sim_baseline, b_info["min"], b_info["max"])
            elif "min" in b_info:
                sim_baseline = np.maximum(sim_baseline, b_info["min"])
            psi_score = calculate_psi(sim_baseline, values)

            if psi_score >= self.psi_critical:
                sev = DriftSeverity.CRITICAL
            elif psi_score >= self.psi_warning:
                sev = DriftSeverity.WARNING
            else:
                sev = DriftSeverity.NONE

            feature_metrics.append(
                FeatureDriftMetric(
                    feature_name=feat,
                    baseline_mean=round(b_mean, 3),
                    current_mean=round(cur_mean, 3),
                    baseline_std=round(b_std, 3),
                    current_std=round(cur_std, 3),
                    psi_score=round(psi_score, 4),
                    drift_severity=sev,
                    details=f"PSI={psi_score:.4f} against baseline mean {b_mean:.2f}",
                )
            )

        critical_count = sum(
            1 for m in feature_metrics if m.drift_severity == DriftSeverity.CRITICAL
        )
        # Flag input feature drift when at least 2 features drift or any feature has severe PSI >= 0.50
        severe_drift = any(m.psi_score >= 0.50 for m in feature_metrics)
        report.feature_metrics = feature_metrics
        report.input_drift_detected = critical_count >= 2 or severe_drift

        # 3. Prediction Distribution Check
        if recent_predictions and len(recent_predictions) >= 10:
            observed_alert_rate = sum(1 for p in recent_predictions if p > 0.5) / len(
                recent_predictions
            )
            pred_shift = abs(observed_alert_rate - baseline_prediction_alert_rate)
            report.prediction_shift_summary = {
                "observed_alert_rate": round(observed_alert_rate, 4),
                "baseline_alert_rate": round(baseline_prediction_alert_rate, 4),
                "delta": round(pred_shift, 4),
            }
            if pred_shift >= 0.20:
                report.prediction_drift_detected = True

        # Synthesize final category
        if report.input_drift_detected:
            report.overall_category = DriftCategory.INPUT_FEATURE_DRIFT
            report.summary_message = "Statistically significant input feature drift detected."
            increment_counter("ml.drift.alerts_total")
        elif report.prediction_drift_detected:
            report.overall_category = DriftCategory.PREDICTION_DRIFT
            report.summary_message = "Significant deviation in model prediction alert frequency."
            increment_counter("ml.drift.alerts_total")
        else:
            report.overall_category = DriftCategory.NO_DRIFT
            report.summary_message = (
                "All monitored telemetry and feature distributions are healthy."
            )

        return report
