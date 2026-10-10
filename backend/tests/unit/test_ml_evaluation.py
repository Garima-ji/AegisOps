"""Unit tests for ML evaluation reporting, baseline comparison, and metric calculation."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.baselines import RollingMadAnomalyDetector, ThresholdAnomalyDetector
from app.ml.evaluation import (
    compute_event_detection_delay,
    evaluate_anomaly_detector,
    evaluate_incident_predictor,
    generate_full_evaluation_report,
)
from app.ml.features import STANDARD_FEATURE_NAMES
from app.ml.incident_predictor import IncidentRiskPredictor
from app.ml.isolation_forest import IsolationForestDetector


def test_evaluate_anomaly_detector_and_report_generation() -> None:
    """Verify evaluation metric computation, false alert rates, and JSON serialization."""
    now = datetime.now(UTC)
    n_samples = 30

    # Synthetic test DataFrame
    rows = []
    for i in range(n_samples):
        r = dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0)
        r["req_count"] = 20.0
        r["req_rate_per_sec"] = 2.0
        r["timestamp"] = now
        r["service_name"] = "payment-service"
        r["scenario"] = "latency_spike" if i >= 20 else "normal"
        r["latency_p95_ms"] = 900.0 if i >= 20 else 80.0
        r["freshness_lag_seconds"] = 2.0
        rows.append(r)

    test_df = pd.DataFrame(rows)
    ground_truth = [1 if i >= 20 else 0 for i in range(n_samples)]

    threshold_det = ThresholdAnomalyDetector(latency_p95_threshold_ms=500.0)
    rolling_mad_det = RollingMadAnomalyDetector()

    iso = IsolationForestDetector()
    iso.fit(test_df.iloc[:15][STANDARD_FEATURE_NAMES])

    pred = IncidentRiskPredictor(min_positive_samples=5)
    # Fit with 10 positive labels
    y_train = np.array([1] * 10 + [0] * 10)
    pred.fit(test_df.iloc[:20][STANDARD_FEATURE_NAMES], y_train)

    eval_det = evaluate_anomaly_detector(threshold_det, test_df, ground_truth)
    assert eval_det["true_positives"] == 10
    assert eval_det["false_positives"] == 0
    assert eval_det["precision"] == 1.0
    assert eval_det["recall"] == 1.0
    assert eval_det["f1_score"] == 1.0
    assert "latency_spike" in eval_det["scenarios"]

    eval_pred = evaluate_incident_predictor(pred, test_df, ground_truth)
    assert eval_pred["status"] == "AVAILABLE"
    assert "precision" in eval_pred
    assert "pr_auc" in eval_pred

    with tempfile.TemporaryDirectory() as tmpdir:
        report_path = Path(tmpdir) / "eval.json"
        full_report = generate_full_evaluation_report(
            threshold_detector=threshold_det,
            rolling_mad_detector=rolling_mad_det,
            isolation_forest_detector=iso,
            incident_predictor=pred,
            test_df=test_df,
            ground_truth_labels=ground_truth,
            incident_target_labels=ground_truth,
            output_path=report_path,
        )
        assert report_path.exists()
        assert "threshold_baseline" in full_report["anomaly_detectors"]
        assert "isolation_forest" in full_report["anomaly_detectors"]


def test_detection_delay_on_time_alert() -> None:
    """Verify that an alert firing at the exact fault onset timestamp has delay_seconds == 0.0."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    faults = [
        {
            "experiment_id": "exp-001",
            "service_name": "payment-service",
            "start_time": t0,
            "end_time": t0 + timedelta(seconds=120),
            "scenario": "latency_spike",
        }
    ]
    alerts = [{"timestamp": t0, "service_name": "payment-service", "anomaly_score": 0.85}]

    res = compute_event_detection_delay(alerts=alerts, fault_intervals=faults)
    assert res["total_incidents"] == 1
    assert res["detected_incidents"] == 1
    assert res["missed_incidents"] == 0
    assert res["detection_rate"] == 1.0
    assert res["delays_seconds"] == [0.0]
    assert res["summary"]["mean_seconds"] == 0.0
    assert res["incident_details"][0]["detected"] is True
    assert res["incident_details"][0]["delay_seconds"] == 0.0


def test_detection_delay_delayed_alert() -> None:
    """Verify that an alert firing 25 seconds after fault onset measures a 25.0s delay."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    faults = [
        {
            "experiment_id": "exp-002",
            "service_name": "payment-service",
            "start_time": t0,
            "end_time": t0 + timedelta(seconds=120),
            "scenario": "error_surge",
        }
    ]
    alerts = [
        {
            "timestamp": t0 + timedelta(seconds=25),
            "service_name": "payment-service",
            "anomaly_score": 0.9,
        }
    ]

    res = compute_event_detection_delay(alerts=alerts, fault_intervals=faults)
    assert res["detected_incidents"] == 1
    assert res["delays_seconds"] == [25.0]
    assert res["summary"]["mean_seconds"] == 25.0
    assert res["summary"]["median_seconds"] == 25.0
    assert res["summary"]["p95_seconds"] == 25.0


def test_detection_delay_missed_incident() -> None:
    """Verify that a fault with zero matching alerts is categorized as missed with delay=None."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    faults = [
        {
            "experiment_id": "exp-003",
            "service_name": "payment-service",
            "start_time": t0,
            "end_time": t0 + timedelta(seconds=120),
            "scenario": "service_timeout",
        }
    ]
    # Alert is on a completely different service
    alerts = [{"timestamp": t0 + timedelta(seconds=10), "service_name": "inventory-service"}]

    res = compute_event_detection_delay(alerts=alerts, fault_intervals=faults)
    assert res["total_incidents"] == 1
    assert res["detected_incidents"] == 0
    assert res["missed_incidents"] == 1
    assert res["detection_rate"] == 0.0
    assert res["delays_seconds"] == []
    assert res["summary"]["mean_seconds"] is None
    assert res["incident_details"][0]["detected"] is False
    assert res["incident_details"][0]["delay_seconds"] is None


def test_detection_delay_duplicate_alerts() -> None:
    """Verify that subsequent repeated alerts do not alter the first qualifying alert's delay."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    faults = [
        {
            "experiment_id": "exp-004",
            "service_name": "payment-service",
            "start_time": t0,
            "end_time": t0 + timedelta(seconds=120),
            "scenario": "latency_spike",
        }
    ]
    # Three consecutive alerts at +10s, +20s, +40s
    alerts = [
        {"timestamp": t0 + timedelta(seconds=10), "service_name": "payment-service"},
        {"timestamp": t0 + timedelta(seconds=20), "service_name": "payment-service"},
        {"timestamp": t0 + timedelta(seconds=40), "service_name": "payment-service"},
    ]

    res = compute_event_detection_delay(alerts=alerts, fault_intervals=faults)
    assert res["detected_incidents"] == 1
    assert res["delays_seconds"] == [10.0]
    assert res["incident_details"][0]["qualifying_alerts_count"] == 3
    assert res["incident_details"][0]["delay_seconds"] == 10.0


def test_detection_delay_alert_preceding_fault_onset() -> None:
    """Verify that an alert firing before fault onset is categorized as pre-fault and does NOT detect."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    faults = [
        {
            "experiment_id": "exp-005",
            "service_name": "payment-service",
            "start_time": t0,
            "end_time": t0 + timedelta(seconds=120),
            "scenario": "memory_leak",
        }
    ]
    # Alert occurs 30s BEFORE fault onset
    alerts = [{"timestamp": t0 - timedelta(seconds=30), "service_name": "payment-service"}]

    res = compute_event_detection_delay(alerts=alerts, fault_intervals=faults)
    assert res["detected_incidents"] == 0
    assert res["missed_incidents"] == 1
    assert res["incident_details"][0]["detected"] is False
    assert res["incident_details"][0]["pre_fault_alerts_count"] == 1
    assert res["incident_details"][0]["qualifying_alerts_count"] == 0


def test_detection_delay_zero_incidents_and_empty_dataset() -> None:
    """Verify safe zero-incident handling with no division errors."""
    res = compute_event_detection_delay(alerts=[], fault_intervals=[])
    assert res["total_incidents"] == 0
    assert res["detected_incidents"] == 0
    assert res["missed_incidents"] == 0
    assert res["detection_rate"] == 0.0
    assert res["summary"]["mean_seconds"] is None
    assert res["summary"]["median_seconds"] is None


def test_detection_delay_scenario_breakdown() -> None:
    """Verify multi-scenario aggregation and breakdown."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)
    faults = [
        {
            "experiment_id": "exp-1",
            "service_name": "payment-service",
            "start_time": t0,
            "end_time": t0 + timedelta(seconds=60),
            "scenario": "latency_spike",
        },
        {
            "experiment_id": "exp-2",
            "service_name": "payment-service",
            "start_time": t0 + timedelta(seconds=200),
            "end_time": t0 + timedelta(seconds=260),
            "scenario": "error_surge",
        },
    ]
    alerts = [
        {"timestamp": t0 + timedelta(seconds=15), "service_name": "payment-service"},
        # No alert for exp-2 (error_surge missed)
    ]

    res = compute_event_detection_delay(alerts=alerts, fault_intervals=faults)
    assert res["total_incidents"] == 2
    assert res["detected_incidents"] == 1
    assert res["missed_incidents"] == 1
    assert res["detection_rate"] == 0.5
    assert "latency_spike" in res["by_scenario"]
    assert res["by_scenario"]["latency_spike"]["detection_rate"] == 1.0
    assert res["by_scenario"]["latency_spike"]["mean_delay_seconds"] == 15.0
    assert "error_surge" in res["by_scenario"]
    assert res["by_scenario"]["error_surge"]["detection_rate"] == 0.0
    assert res["by_scenario"]["error_surge"]["missed_incidents"] == 1


def test_evaluate_incident_predictor_excludes_embargoed_samples() -> None:
    """Verify evaluate_incident_predictor explicitly drops is_embargoed == True samples."""
    now = datetime.now(UTC)
    rows = []
    for i in range(20):
        r = dict.fromkeys(STANDARD_FEATURE_NAMES, 0.0)
        r["timestamp"] = now
        r["service_name"] = "payment-service"
        r["is_embargoed"] = True if i < 5 else False
        rows.append(r)

    test_df = pd.DataFrame(rows)
    # 5 embargoed samples (target -1), 5 positive samples (1), 10 negative samples (0)
    y_test = [-1] * 5 + [1] * 5 + [0] * 10

    pred = IncidentRiskPredictor(min_positive_samples=5)
    # Fit with clean samples
    pred.fit(test_df.iloc[5:][STANDARD_FEATURE_NAMES], y_test[5:])

    eval_res = evaluate_incident_predictor(pred, test_df, y_test)
    assert eval_res["status"] == "AVAILABLE"
    # All 5 embargoed samples were excluded
    assert eval_res["embargoed_samples_excluded"] == 5
    assert eval_res["n_samples"] == 15
