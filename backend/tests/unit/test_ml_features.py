"""Unit tests for ML feature extraction, windowing, leakage guards, and label construction."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.ml.features import (
    STANDARD_FEATURE_NAMES,
    attach_incident_labels,
    build_sliding_feature_dataset,
    extract_window_features,
)
from app.schemas.telemetry import MetricPoint


def _make_metric(service: str, name: str, val: float, ts: datetime) -> MetricPoint:
    return MetricPoint(
        service_name=service,
        metric_name=name,
        value=val,
        unit="ms" if "duration" in name else "count",
        timestamp=ts,
    )


def test_standard_feature_schema_completeness() -> None:
    """Ensure canonical feature schema contains all required dimensions."""
    assert len(STANDARD_FEATURE_NAMES) == 15
    assert "req_rate_per_sec" in STANDARD_FEATURE_NAMES
    assert "latency_p95_ms" in STANDARD_FEATURE_NAMES
    assert "error_rate" in STANDARD_FEATURE_NAMES
    assert "freshness_lag_seconds" in STANDARD_FEATURE_NAMES
    assert "missingness_ratio" in STANDARD_FEATURE_NAMES


def test_strict_temporal_leakage_protection() -> None:
    """CRITICAL TEST: Demonstrate future observations cannot affect earlier feature vectors.

    A feature vector computed at T_cutoff must be 100% IDENTICAL whether or not future
    telemetry (T > T_cutoff) is present in the observation pool.
    """
    t_base = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    t_cutoff = t_base + timedelta(seconds=60)

    # 1. Past observations within [t_base, t_cutoff]
    past_metrics = [
        _make_metric(
            "payment-service", "http.server.requests.count", 10.0, t_base + timedelta(seconds=10)
        ),
        _make_metric(
            "payment-service",
            "http.server.request.duration_ms",
            120.0,
            t_base + timedelta(seconds=20),
        ),
        _make_metric(
            "payment-service",
            "http.server.request.duration_ms",
            180.0,
            t_base + timedelta(seconds=30),
        ),
        _make_metric(
            "payment-service", "http.server.errors.count", 1.0, t_base + timedelta(seconds=40)
        ),
    ]

    features_isolated = extract_window_features(
        service_name="payment-service",
        window_end=t_cutoff,
        window_seconds=60.0,
        metrics=past_metrics,
    )

    # 2. Inject extreme future outliers at T > t_cutoff (e.g. latency = 99999ms, error_count = 1000)
    future_metrics = [
        _make_metric(
            "payment-service",
            "http.server.request.duration_ms",
            99999.0,
            t_cutoff + timedelta(seconds=1),
        ),
        _make_metric(
            "payment-service",
            "http.server.request.duration_ms",
            50000.0,
            t_cutoff + timedelta(seconds=10),
        ),
        _make_metric(
            "payment-service", "http.server.errors.count", 1000.0, t_cutoff + timedelta(seconds=30)
        ),
    ]

    contaminated_pool = past_metrics + future_metrics

    features_with_future = extract_window_features(
        service_name="payment-service",
        window_end=t_cutoff,
        window_seconds=60.0,
        metrics=contaminated_pool,
    )

    # Assert exact numerical equivalence across every single feature dimension
    for feat_name in STANDARD_FEATURE_NAMES:
        assert features_isolated[feat_name] == pytest.approx(
            features_with_future[feat_name], abs=1e-6
        ), (
            f"Leakage detected on '{feat_name}': {features_isolated[feat_name]} != {features_with_future[feat_name]}"
        )


def test_utc_normalization_naive_and_aware() -> None:
    """Ensure naive and aware timestamps are normalized to UTC seamlessly."""
    t_naive = datetime(2026, 10, 10, 10, 0, 0)
    t_aware = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)

    metric_naive = _make_metric("order-service", "http.server.requests.count", 5.0, t_naive)
    metric_aware = _make_metric("order-service", "http.server.requests.count", 5.0, t_aware)

    f_naive = extract_window_features(
        service_name="order-service",
        window_end=t_aware + timedelta(seconds=30),
        window_seconds=60.0,
        metrics=[metric_naive],
    )
    f_aware = extract_window_features(
        service_name="order-service",
        window_end=t_aware + timedelta(seconds=30),
        window_seconds=60.0,
        metrics=[metric_aware],
    )

    assert f_naive["req_count"] == f_aware["req_count"] == 5.0


def test_empty_window_safe_defaults() -> None:
    """Empty observation windows must return safe, non-crash defaults with high freshness lag."""
    t_cutoff = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    feats = extract_window_features(
        service_name="non-existent-service",
        window_end=t_cutoff,
        window_seconds=60.0,
        metrics=[],
        logs=[],
        spans=[],
    )

    assert feats["req_count"] == 0.0
    assert feats["req_rate_per_sec"] == 0.0
    assert feats["error_rate"] == 0.0
    assert feats["latency_p95_ms"] == 0.0
    assert feats["freshness_lag_seconds"] >= 60.0
    assert feats["missingness_ratio"] == 1.0


def test_sliding_feature_dataset_generator() -> None:
    """Verify build_sliding_feature_dataset produces ordered chronological dataframe."""
    t_start = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    metrics = [
        _make_metric(
            "service-a", "http.server.requests.count", 10.0, t_start + timedelta(seconds=10)
        ),
        _make_metric(
            "service-a", "http.server.requests.count", 15.0, t_start + timedelta(seconds=40)
        ),
        _make_metric(
            "service-a", "http.server.requests.count", 20.0, t_start + timedelta(seconds=80)
        ),
    ]

    df = build_sliding_feature_dataset(
        metrics=metrics,
        logs=[],
        spans=[],
        services=["service-a"],
        window_seconds=30.0,
        step_seconds=15.0,
    )

    assert not df.empty
    assert "timestamp" in df.columns
    assert "service_name" in df.columns
    for feat in STANDARD_FEATURE_NAMES:
        assert feat in df.columns
    # Monotonically increasing timestamps
    assert df["timestamp"].is_monotonic_increasing


def test_attach_incident_labels_and_embargo() -> None:
    """Verify incident target labeling and embargo buffer isolation."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    df = pd.DataFrame(
        [
            {
                "timestamp": t0 + timedelta(seconds=100),
                "service_name": "payment-service",
                **dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0),
            },
            {
                "timestamp": t0 + timedelta(seconds=250),
                "service_name": "payment-service",
                **dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0),
            },
            {
                "timestamp": t0 + timedelta(seconds=450),
                "service_name": "payment-service",
                **dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0),
            },
            {
                "timestamp": t0 + timedelta(seconds=650),
                "service_name": "payment-service",
                **dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0),
            },
        ]
    )

    faults = [
        {
            "target_service": "payment-service",
            "start_time": t0 + timedelta(seconds=400),
            "end_time": t0 + timedelta(seconds=500),
            "scenario": "latency_spike",
        }
    ]

    labeled = attach_incident_labels(
        df=df,
        experiments=faults,
        horizon_seconds=300.0,
        embargo_seconds=60.0,
    )

    assert "target_incident_in_horizon" in labeled.columns
    assert "is_incident" in labeled.columns
    assert "is_embargoed" in labeled.columns

    # Row 0: t=100. Fault starts at 400. 100 < 400 <= 400 (horizon is 100+300=400). Target=1!
    assert labeled.iloc[0]["target_incident_in_horizon"] == 1
    assert labeled.iloc[0]["is_incident"] == 0

    # Row 1: t=250. Fault starts at 400. 250 < 400 <= 550. Target=1!
    assert labeled.iloc[1]["target_incident_in_horizon"] == 1
    assert labeled.iloc[1]["is_incident"] == 0

    # Row 2: t=450. Falls DURING active fault (400 to 500). Active incident=1, embargoed=True
    assert labeled.iloc[2]["is_incident"] == 1
    assert labeled.iloc[2]["scenario"] == "latency_spike"
    assert bool(labeled.iloc[2]["is_embargoed"]) is True

    # Row 3: t=650. Well after fault. Target=0, Active=0
    assert labeled.iloc[3]["target_incident_in_horizon"] == 0
    assert labeled.iloc[3]["is_incident"] == 0
    assert bool(labeled.iloc[3]["is_embargoed"]) is False


def test_embargoed_rows_cannot_enter_training_or_evaluation() -> None:
    """Verify that embargoed samples (target -1) are excluded from IncidentRiskPredictor.fit and evaluation."""
    from app.ml.incident_predictor import IncidentRiskPredictor

    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    rows = []
    # 6 positive rows (t=10..60), 4 active fault rows (t=100..130), 10 normal rows (t=200..290)
    for i in range(20):
        r = dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0)
        r["timestamp"] = t0 + timedelta(seconds=i * 15)
        r["service_name"] = "payment-service"
        rows.append(r)

    df = pd.DataFrame(rows)
    # Fault from t=100 to t=130 (seconds 100..130)
    faults = [
        {
            "target_service": "payment-service",
            "start_time": t0 + timedelta(seconds=100),
            "end_time": t0 + timedelta(seconds=130),
            "scenario": "latency_spike",
        }
    ]

    labeled = attach_incident_labels(
        df=df, experiments=faults, horizon_seconds=120.0, embargo_seconds=10.0
    )

    # Active/embargoed rows must be flagged with is_embargoed == True and target == -1 (not relabeled as normal 0)
    embargoed_rows = labeled[labeled["is_embargoed"]]
    assert len(embargoed_rows) > 0
    assert (embargoed_rows["target_incident_in_horizon"] == -1).all()

    # Unambiguous rows must only have binary 0 or 1
    unambiguous_rows = labeled[~labeled["is_embargoed"]]
    assert set(unambiguous_rows["target_incident_in_horizon"].unique()).issubset({0, 1})

    # When fitting IncidentRiskPredictor with embedded is_embargoed column, it automatically drops them
    predictor = IncidentRiskPredictor(min_positive_samples=3)
    predictor.fit(
        X_train=labeled[["is_embargoed", *STANDARD_FEATURE_NAMES]],
        y_train=labeled["target_incident_in_horizon"],
    )
    assert predictor.is_fitted is True
    assert predictor.metadata["embargoed_samples_excluded"] == len(embargoed_rows)

    # If someone directly passes invalid labels with no embargo filtering, fit raises ValueError
    bad_predictor = IncidentRiskPredictor(min_positive_samples=3)
    with pytest.raises(ValueError, match="Only unambiguous binary targets"):
        bad_predictor.fit(
            X_train=labeled[STANDARD_FEATURE_NAMES],
            y_train=np.array([-1] * 4 + [1] * 8 + [0] * 8),
        )


def test_feature_columns_contain_no_ground_truth_or_leakage() -> None:
    """Verify that canonical feature schema contains zero ground-truth labels and rejects forbidden columns."""
    from app.ml.incident_predictor import IncidentRiskPredictor

    forbidden = {"target", "target_incident_in_horizon", "is_incident", "is_embargoed", "scenario"}
    assert set(STANDARD_FEATURE_NAMES).isdisjoint(forbidden)

    # Predictor explicitly rejects forbidden columns if passed in feature_names
    pred = IncidentRiskPredictor(feature_names=["req_count", "is_incident"])
    with pytest.raises(ValueError, match="is a label/metadata column"):
        pred._validate_and_extract_matrix(pd.DataFrame([{"req_count": 1.0, "is_incident": 1.0}]))
