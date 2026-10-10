"""Unit tests for ML models: statistical baselines, Isolation Forest, and incident risk predictor."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.baselines import (
    RollingMadAnomalyDetector,
    ThresholdAnomalyDetector,
    compute_severity,
)
from app.ml.features import STANDARD_FEATURE_NAMES
from app.ml.incident_predictor import IncidentRiskPredictor, compute_risk_level
from app.ml.isolation_forest import IsolationForestDetector


def _generate_synthetic_nominal_df(n_samples: int = 100) -> pd.DataFrame:
    """Generate deterministic normal operating feature vectors."""
    rng = np.random.RandomState(42)
    data = {
        "req_count": rng.poisson(lam=20, size=n_samples).astype(float),
        "req_rate_per_sec": rng.normal(loc=5.0, scale=0.5, size=n_samples),
        "error_count": np.zeros(n_samples, dtype=float),
        "error_rate": np.zeros(n_samples, dtype=float),
        "latency_mean_ms": rng.normal(loc=80.0, scale=10.0, size=n_samples),
        "latency_median_ms": rng.normal(loc=78.0, scale=8.0, size=n_samples),
        "latency_p95_ms": rng.normal(loc=120.0, scale=15.0, size=n_samples),
        "latency_p99_ms": rng.normal(loc=150.0, scale=20.0, size=n_samples),
        "latency_std_ms": rng.normal(loc=15.0, scale=3.0, size=n_samples),
        "cpu_utilization_mean": rng.uniform(0.15, 0.35, size=n_samples),
        "memory_utilization_mean": rng.uniform(0.40, 0.55, size=n_samples),
        "child_span_count": rng.poisson(lam=2, size=n_samples).astype(float),
        "error_span_count": np.zeros(n_samples, dtype=float),
        "missingness_ratio": np.zeros(n_samples, dtype=float),
        "freshness_lag_seconds": rng.uniform(1.0, 5.0, size=n_samples),
    }
    return pd.DataFrame(data)


def test_severity_and_risk_mappings() -> None:
    """Validate operational severity and risk mapping functions."""
    assert compute_severity(0.95) == "CRITICAL"
    assert compute_severity(0.70) == "HIGH"
    assert compute_severity(0.50) == "MEDIUM"
    assert compute_severity(0.25) == "LOW"
    assert compute_severity(0.10) == "INFO"

    assert compute_risk_level(0.85) == "CRITICAL"
    assert compute_risk_level(0.60) == "HIGH"
    assert compute_risk_level(0.30) == "MEDIUM"
    assert compute_risk_level(0.10) == "LOW"


def test_threshold_detector_rules() -> None:
    """Test deterministic threshold rules on nominal vs anomalous features."""
    detector = ThresholdAnomalyDetector(
        error_rate_threshold=0.10,
        latency_p95_threshold_ms=500.0,
        freshness_lag_threshold_sec=120.0,
    )
    now = datetime.now(UTC)

    # 1. Nominal payload
    nominal = dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0)
    nominal["req_count"] = 50.0
    nominal["latency_p95_ms"] = 120.0
    res_nom = detector.detect(nominal, "order-service", now)
    assert not res_nom.is_anomaly
    assert res_nom.severity == "INFO"

    # 2. Error rate breach
    high_err = dict(nominal)
    high_err["error_rate"] = 0.40
    res_err = detector.detect(high_err, "order-service", now)
    assert res_err.is_anomaly
    assert "error_rate" in res_err.triggering_features

    # 3. Latency breach
    high_lat = dict(nominal)
    high_lat["latency_p95_ms"] = 850.0
    res_lat = detector.detect(high_lat, "order-service", now)
    assert res_lat.is_anomaly
    assert "latency_p95_ms" in res_lat.triggering_features


def test_rolling_mad_zero_variance_safety() -> None:
    """Demonstrate Rolling MAD detector does not divide by zero or crash on completely flat streams."""
    detector = RollingMadAnomalyDetector(z_threshold=3.5, history_window=20)
    now = datetime.now(UTC)

    flat_features = dict.fromkeys(STANDARD_FEATURE_NAMES, 10.0)

    # Feed 25 identical observations (MAD = 0 across all dimensions)
    for _ in range(25):
        res = detector.detect(flat_features, "payment-service", now)
        assert not res.is_anomaly  # Flat constant signal should not trigger false alarm

    # Now inject a sudden metric spike
    spike_features = dict(flat_features)
    spike_features["latency_p95_ms"] = 1000.0
    res_spike = detector.detect(spike_features, "payment-service", now)
    assert res_spike.is_anomaly
    assert "latency_p95_ms" in res_spike.triggering_features


def test_isolation_forest_fit_predict_and_persistence() -> None:
    """Test Isolation Forest pipeline fitting, normalized score output, and disk serialization."""
    df_nominal = _generate_synthetic_nominal_df(n_samples=150)
    detector = IsolationForestDetector(model_version="v1.0.0", contamination=0.05, random_state=42)

    detector.fit(df_nominal)
    assert detector.is_fitted
    assert detector.metadata["n_samples"] == 150

    now = datetime.now(UTC)

    # 1. Nominal sample score should be low
    nom_sample = df_nominal.iloc[0].to_dict()
    res_nom = detector.detect(nom_sample, "inventory-service", now)
    assert res_nom.anomaly_score < 0.60
    assert not res_nom.is_anomaly

    # 2. Outlier sample score should be high
    outlier = dict(nom_sample)
    outlier["latency_p95_ms"] = 3500.0
    outlier["error_rate"] = 0.90
    outlier["cpu_utilization_mean"] = 0.98
    res_out = detector.detect(outlier, "inventory-service", now)
    assert res_out.anomaly_score >= 0.60
    assert res_out.is_anomaly
    assert len(res_out.triggering_features) > 0

    # 3. Model save and reload
    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = Path(tmpdir) / "iforest.joblib"
        detector.save(model_path)
        assert model_path.exists()

        reloaded = IsolationForestDetector.load(model_path)
        assert reloaded.is_fitted
        res_reloaded = reloaded.detect(outlier, "inventory-service", now)
        assert res_reloaded.anomaly_score == res_out.anomaly_score


def test_isolation_forest_schema_validation_error() -> None:
    """Ensure missing required features raise explicit ValueError."""
    detector = IsolationForestDetector()
    detector.fit(_generate_synthetic_nominal_df(50))

    incomplete_df = pd.DataFrame({"req_count": [1.0, 2.0]})
    with pytest.raises(ValueError, match="Missing required feature"):
        detector.score_features(incomplete_df)


def test_incident_predictor_min_label_guard() -> None:
    """Verify supervised Incident Risk Predictor rejects training if positive samples < min."""
    df = _generate_synthetic_nominal_df(50)
    # Only 2 positive samples (< required 5)
    y = np.array([1, 1] + [0] * 48)

    predictor = IncidentRiskPredictor(min_positive_samples=5)
    with pytest.raises(ValueError, match="Insufficient positive incident labels"):
        predictor.fit(df, y)

    assert not predictor.is_fitted
    assert predictor.status == "UNAVAILABLE"


def test_incident_predictor_fit_predict_and_persistence() -> None:
    """Verify supervised Incident Risk Predictor fits on valid labels and estimates probabilities."""
    df = _generate_synthetic_nominal_df(100)
    # 20 positive, 80 negative
    y = np.array([1] * 20 + [0] * 80)

    predictor = IncidentRiskPredictor(min_positive_samples=5, random_state=42)
    predictor.fit(df, y)
    assert predictor.is_fitted
    assert predictor.status == "AVAILABLE"

    now = datetime.now(UTC)
    pred_res = predictor.predict(df.iloc[0].to_dict(), "payment-service", now)

    assert 0.0 <= pred_res.risk_probability <= 1.0
    assert pred_res.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "predictor.joblib"
        predictor.save(save_path)
        reloaded = IncidentRiskPredictor.load(save_path)
        assert reloaded.is_fitted
        assert reloaded.status == "AVAILABLE"
