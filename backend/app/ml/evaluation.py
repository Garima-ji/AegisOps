"""Evaluation framework for anomaly detectors and incident risk classifiers.

Provides leakage-safe chronological evaluation, baseline comparisons, false-alert rate calculations,
detection delay tracking, and machine-readable JSON evaluation reporting.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

from app.ml.baselines import RollingMadAnomalyDetector, ThresholdAnomalyDetector
from app.ml.incident_predictor import IncidentRiskPredictor
from app.ml.isolation_forest import IsolationForestDetector


def _to_utc(dt: datetime) -> datetime:
    """Ensure timestamp is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class _NormAlert(NamedTuple):
    timestamp: datetime
    service_name: str
    anomaly_score: float


def compute_event_detection_delay(
    alerts: list[dict[str, Any]],
    fault_intervals: list[dict[str, Any]],
    tolerance_seconds: float = 0.0,
) -> dict[str, Any]:
    """Compute event-level detection delay metrics for fault intervals.

    Definition:
      Elapsed time in seconds from fault interval start_time to the first qualifying
      true-positive alert for that incident.

    Matching Rules:
      1. Service Matching: Alert service_name must equal incident service_name / target_service.
      2. Temporal Qualification: Alert timestamp must fall within [start_time, end_time + tolerance_seconds].
      3. Pre-Fault Alerts: Alerts before start_time (timestamp < start_time) are classified as False Alerts (unmatched),
         and do NOT qualify as incident detections.
      4. Repeated Alerts: Only the first qualifying alert defines the detection timestamp and delay.
         Subsequent alerts during the same incident interval are counted as redundant/corroborating alerts.
      5. Missed Incidents: If no qualifying alert occurs within [start_time, end_time + tolerance_seconds],
         the incident is classified as MISSED (delay is None, missed_incidents incremented).
    """
    total_incidents = len(fault_intervals)
    if total_incidents == 0:
        return {
            "total_incidents": 0,
            "detected_incidents": 0,
            "missed_incidents": 0,
            "detection_rate": 0.0,
            "delays_seconds": [],
            "summary": {
                "mean_seconds": None,
                "median_seconds": None,
                "min_seconds": None,
                "max_seconds": None,
                "p95_seconds": None,
                "std_seconds": None,
            },
            "by_scenario": {},
            "incident_details": [],
        }

    # Normalize alerts
    norm_alerts: list[_NormAlert] = []
    for a in alerts:
        ts = a.get("timestamp")
        if not isinstance(ts, datetime):
            ts = pd.to_datetime(ts).to_pydatetime()
        norm_alerts.append(
            _NormAlert(
                timestamp=_to_utc(ts),
                service_name=str(a.get("service_name", "")),
                anomaly_score=float(a.get("anomaly_score", 1.0)),
            )
        )

    incident_results: list[dict[str, Any]] = []
    delays: list[float] = []

    for idx, exp in enumerate(fault_intervals):
        st_raw = exp["start_time"]
        et_raw = exp["end_time"]
        st = _to_utc(
            st_raw if isinstance(st_raw, datetime) else datetime.fromisoformat(str(st_raw))
        )
        et = _to_utc(
            et_raw if isinstance(et_raw, datetime) else datetime.fromisoformat(str(et_raw))
        )
        svc = str(exp.get("target_service") or exp.get("service_name", ""))
        scenario = str(exp.get("scenario", "fault"))
        exp_id = str(exp.get("experiment_id", f"exp-{idx:03d}"))

        # Alerts for this specific service
        svc_alerts = [a for a in norm_alerts if a.service_name == svc]

        # Categorize
        pre_fault_alerts = [a for a in svc_alerts if a.timestamp < st]
        qualifying_alerts = [
            a
            for a in svc_alerts
            if st <= a.timestamp <= (et + timedelta(seconds=tolerance_seconds))
        ]

        first_ts: datetime | None = None
        delay: float | None = None
        if qualifying_alerts:
            qualifying_alerts.sort(key=lambda x: x.timestamp)
            first_alert = qualifying_alerts[0]
            first_ts = first_alert.timestamp
            delay = round((first_ts - st).total_seconds(), 2)
            delays.append(delay)
            detected = True
        else:
            detected = False

        incident_results.append(
            {
                "incident_id": exp_id,
                "scenario": scenario,
                "service_name": svc,
                "start_time": st.isoformat(),
                "end_time": et.isoformat(),
                "detected": detected,
                "delay_seconds": delay,
                "first_alert_time": first_ts.isoformat() if first_ts else None,
                "pre_fault_alerts_count": len(pre_fault_alerts),
                "qualifying_alerts_count": len(qualifying_alerts),
            }
        )

    detected_count = sum(1 for inc in incident_results if inc["detected"])
    missed_count = total_incidents - detected_count
    detection_rate = round(detected_count / total_incidents, 4)

    summary: dict[str, float | None]
    if delays:
        arr = np.array(delays, dtype=float)
        summary = {
            "mean_seconds": round(float(np.mean(arr)), 2),
            "median_seconds": round(float(np.median(arr)), 2),
            "min_seconds": round(float(np.min(arr)), 2),
            "max_seconds": round(float(np.max(arr)), 2),
            "p95_seconds": round(float(np.percentile(arr, 95)), 2),
            "std_seconds": round(float(np.std(arr)), 2) if len(arr) > 1 else 0.0,
        }
    else:
        summary = {
            "mean_seconds": None,
            "median_seconds": None,
            "min_seconds": None,
            "max_seconds": None,
            "p95_seconds": None,
            "std_seconds": None,
        }

    # Breakdown by scenario
    by_scenario: dict[str, Any] = {}
    scenarios = {inc["scenario"] for inc in incident_results}
    for scn in sorted(scenarios):
        sub_incs = [inc for inc in incident_results if inc["scenario"] == scn]
        sub_delays = [
            inc["delay_seconds"]
            for inc in sub_incs
            if inc["detected"] and inc["delay_seconds"] is not None
        ]
        sub_total = len(sub_incs)
        sub_detected = sum(1 for inc in sub_incs if inc["detected"])
        sub_missed = sub_total - sub_detected
        by_scenario[scn] = {
            "total_incidents": sub_total,
            "detected_incidents": sub_detected,
            "missed_incidents": sub_missed,
            "detection_rate": round(sub_detected / sub_total, 4) if sub_total > 0 else 0.0,
            "mean_delay_seconds": round(float(np.mean(sub_delays)), 2) if sub_delays else None,
            "median_delay_seconds": round(float(np.median(sub_delays)), 2) if sub_delays else None,
            "delays_seconds": sub_delays,
        }

    return {
        "total_incidents": total_incidents,
        "detected_incidents": detected_count,
        "missed_incidents": missed_count,
        "detection_rate": detection_rate,
        "delays_seconds": delays,
        "summary": summary,
        "by_scenario": by_scenario,
        "incident_details": incident_results,
    }


def evaluate_anomaly_detector(
    detector: ThresholdAnomalyDetector | RollingMadAnomalyDetector | IsolationForestDetector,
    test_df: pd.DataFrame,
    ground_truth_anomalies: pd.Series | list[bool],
    service_column: str = "service_name",
    timestamp_column: str = "timestamp",
    window_duration_seconds: float = 60.0,
    fault_intervals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate an anomaly detector against ground-truth incident annotations."""
    y_true = np.asarray(ground_truth_anomalies, dtype=int)
    n_samples = len(test_df)
    if n_samples == 0:
        return {"error": "Empty evaluation dataset"}

    y_pred = []
    scores = []
    timestamps = []
    alerts = []

    for _idx, row in test_df.iterrows():
        feat_dict = {
            col: float(val)
            for col, val in row.items()
            if col
            not in (
                service_column,
                timestamp_column,
                "is_incident",
                "scenario",
                "target",
                "target_incident_in_horizon",
                "is_embargoed",
            )
        }
        service = str(row.get(service_column, "unknown-service"))
        ts = row.get(timestamp_column, datetime.now(UTC))
        if not isinstance(ts, datetime):
            ts = pd.to_datetime(ts).to_pydatetime()
        ts = _to_utc(ts)
        timestamps.append(ts)

        res = detector.detect(features=feat_dict, service_name=service, timestamp=ts)
        is_ano = 1 if res.is_anomaly else 0
        y_pred.append(is_ano)
        scores.append(res.anomaly_score)

        if is_ano == 1:
            alerts.append(
                {
                    "timestamp": ts,
                    "service_name": service,
                    "anomaly_score": res.anomaly_score,
                }
            )

    y_pred_arr = np.asarray(y_pred, dtype=int)
    scores_arr = np.asarray(scores, dtype=float)

    # Standard classification metrics
    cm = confusion_matrix(y_true, y_pred_arr, labels=[0, 1])
    tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])

    prec = float(precision_score(y_true, y_pred_arr, zero_division=0))
    rec = float(recall_score(y_true, y_pred_arr, zero_division=0))
    f1 = float(f1_score(y_true, y_pred_arr, zero_division=0))

    # False alerts per service-hour calculation:
    exposure_hours = max(0.01, (n_samples * window_duration_seconds) / 3600.0)
    false_alert_rate_per_hour = round(fp / exposure_hours, 2)

    # PR-AUC
    try:
        pr_auc = float(average_precision_score(y_true, scores_arr))
    except Exception:
        pr_auc = 0.0

    # Scenario recall breakdown
    scenario_metrics: dict[str, Any] = {}
    if "scenario" in test_df.columns:
        for scn in test_df["scenario"].dropna().unique():
            if scn in ("normal", "nominal", ""):
                continue
            mask = test_df["scenario"] == scn
            sub_true = y_true[mask]
            sub_pred = y_pred_arr[mask]
            sub_rec = float(recall_score(sub_true, sub_pred, zero_division=0))
            scenario_metrics[str(scn)] = {
                "sample_count": int(np.sum(mask)),
                "detected": int(np.sum(sub_pred == 1)),
                "recall": round(sub_rec, 4),
            }

    detector_name = getattr(detector, "model_name", detector.__class__.__name__)

    result: dict[str, Any] = {
        "detector_name": detector_name,
        "n_samples": n_samples,
        "exposure_hours": round(exposure_hours, 3),
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1_score": round(f1, 4),
        "pr_auc": round(pr_auc, 4),
        "false_alerts_per_hour": false_alert_rate_per_hour,
        "missed_incidents": fn,
        "scenarios": scenario_metrics,
    }

    # Event-level detection delay evaluation
    if fault_intervals is not None:
        result["detection_delay"] = compute_event_detection_delay(
            alerts=alerts,
            fault_intervals=fault_intervals,
        )

    return result


def evaluate_incident_predictor(
    predictor: IncidentRiskPredictor,
    test_df: pd.DataFrame,
    y_test: pd.Series | list[int],
    service_column: str = "service_name",
    timestamp_column: str = "timestamp",
    is_embargoed: pd.Series | np.ndarray | None = None,
) -> dict[str, Any]:
    """Evaluate supervised incident risk predictor on held-out chronological test partition."""
    if not predictor.is_fitted:
        return {
            "model_name": predictor.model_name,
            "status": "UNAVAILABLE",
            "message": "Predictor is not fitted due to insufficient labels.",
        }

    y_true_arr = np.asarray(y_test, dtype=int)
    test_clean = test_df.copy()
    embargoed_excluded = 0

    # Exclude embargoed samples from evaluation (Finding 2)
    if is_embargoed is not None:
        emb_mask = np.asarray(is_embargoed, dtype=bool)
        keep_mask = ~emb_mask
        embargoed_excluded += int(np.sum(emb_mask))
        test_clean = test_clean.loc[keep_mask].copy()
        y_true_arr = y_true_arr[keep_mask]
    elif "is_embargoed" in test_clean.columns:
        emb_mask = test_clean["is_embargoed"].astype(bool).to_numpy()
        keep_mask = ~emb_mask
        embargoed_excluded += int(np.sum(emb_mask))
        test_clean = test_clean.loc[keep_mask].copy()
        y_true_arr = y_true_arr[keep_mask]

    # Exclude negative labels (-1) that indicate embargoed / ambiguous states
    valid_mask = y_true_arr >= 0
    if not np.all(valid_mask):
        embargoed_excluded += int(np.sum(~valid_mask))
        test_clean = test_clean.loc[valid_mask].copy()
        y_true_arr = y_true_arr[valid_mask]

    n_samples = len(test_clean)
    pos_count = int(np.sum(y_true_arr == 1))

    if n_samples == 0:
        return {
            "model_name": predictor.model_name,
            "status": "UNAVAILABLE",
            "message": "No unambiguous evaluation samples remaining after embargo filtering.",
            "embargoed_samples_excluded": embargoed_excluded,
        }

    # Extract feature matrix excluding metadata
    feat_cols = [c for c in predictor.feature_names if c in test_clean.columns]
    X_sub = test_clean[feat_cols]

    y_prob = predictor.predict_proba(X_sub)
    y_pred = (y_prob >= predictor.risk_threshold).astype(int)

    cm = confusion_matrix(y_true_arr, y_pred, labels=[0, 1])
    tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])

    prec = float(precision_score(y_true_arr, y_pred, zero_division=0))
    rec = float(recall_score(y_true_arr, y_pred, zero_division=0))
    f1 = float(f1_score(y_true_arr, y_pred, zero_division=0))

    try:
        pr_auc = float(average_precision_score(y_true_arr, y_prob))
    except Exception:
        pr_auc = 0.0

    try:
        brier = float(brier_score_loss(y_true_arr, y_prob))
    except Exception:
        brier = 0.0

    # Recall at alert budget (top 20% highest risk scores)
    top_20_cutoff = np.percentile(y_prob, 80)
    top_20_pred = (y_prob >= top_20_cutoff).astype(int)
    recall_at_top20 = float(recall_score(y_true_arr, top_20_pred, zero_division=0))

    return {
        "model_name": predictor.model_name,
        "model_version": predictor.model_version,
        "status": "AVAILABLE",
        "n_samples": n_samples,
        "positive_labels": pos_count,
        "embargoed_samples_excluded": embargoed_excluded,
        "risk_threshold": predictor.risk_threshold,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1_score": round(f1, 4),
        "pr_auc": round(pr_auc, 4),
        "brier_score": round(brier, 4),
        "recall_at_top20_percent_alerts": round(recall_at_top20, 4),
    }


def generate_full_evaluation_report(
    threshold_detector: ThresholdAnomalyDetector,
    rolling_mad_detector: RollingMadAnomalyDetector,
    isolation_forest_detector: IsolationForestDetector,
    incident_predictor: IncidentRiskPredictor | None,
    test_df: pd.DataFrame,
    ground_truth_labels: pd.Series | list[int],
    incident_target_labels: pd.Series | list[int] | None = None,
    output_path: str | Path | None = None,
    evaluation_metadata: dict[str, Any] | None = None,
    fault_intervals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute complete benchmarking protocol and generate versioned JSON report."""
    report: dict[str, Any] = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "protocol": "Chronological holdout with zero future-leakage and embargo exclusion",
        "metadata": evaluation_metadata or {},
        "anomaly_detectors": {},
        "incident_prediction": {},
    }

    # Evaluate deterministic and unsupervised anomaly detectors on exact same test partition
    report["anomaly_detectors"]["threshold_baseline"] = evaluate_anomaly_detector(
        threshold_detector, test_df, ground_truth_labels, fault_intervals=fault_intervals
    )
    report["anomaly_detectors"]["rolling_mad_baseline"] = evaluate_anomaly_detector(
        rolling_mad_detector, test_df, ground_truth_labels, fault_intervals=fault_intervals
    )
    if isolation_forest_detector.is_fitted:
        report["anomaly_detectors"]["isolation_forest"] = evaluate_anomaly_detector(
            isolation_forest_detector, test_df, ground_truth_labels, fault_intervals=fault_intervals
        )

    # Evaluate supervised incident predictor if provided (with automatic embargo exclusion)
    if incident_predictor is not None and incident_target_labels is not None:
        report["incident_prediction"] = evaluate_incident_predictor(
            incident_predictor, test_df, incident_target_labels
        )

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    return report
