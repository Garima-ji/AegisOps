"""Model training and evaluation CLI pipeline for AegisOps (Phase 3).

Trains:
1. Isolation Forest anomaly detector on nominal/representative operating windows.
2. Supervised incident-risk predictor on chronological split with embargo buffer.
3. Evaluates baselines (Threshold, Rolling MAD) vs ML models on the identical test partition.
4. Serializes models to ml/artifacts/ and outputs evaluation_report.json.
5. Records model metadata into PostgreSQL ml_models registry if database is accessible.

Supports two explicit data source modes:
- '--data-source synthetic': Deterministic multi-scenario benchmark telemetry fixtures.
- '--data-source db': Actual PostgreSQL telemetry lake with bounded queries and data readiness validation.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure backend app is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models.experiment import ExperimentModel, FaultLabelModel
from app.db.models.ml import MLModelModel
from app.db.models.telemetry import LogRecordModel, MetricRecordModel, TraceSpanModel
from app.db.session import check_database_connection, get_session_factory
from app.ingestion.generator import DeterministicTelemetryGenerator
from app.ml.baselines import RollingMadAnomalyDetector, ThresholdAnomalyDetector
from app.ml.evaluation import generate_full_evaluation_report
from app.ml.features import (
    STANDARD_FEATURE_NAMES,
    attach_incident_labels,
    build_sliding_feature_dataset,
)
from app.ml.incident_predictor import IncidentRiskPredictor
from app.ml.isolation_forest import IsolationForestDetector


class DatabaseTrainingError(Exception):
    """Raised when database telemetry extraction, connectivity, or readiness validation fails."""


def _to_utc(dt: datetime) -> datetime:
    """Ensure timestamp is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def generate_benchmark_telemetry_dataset() -> tuple[list, list, list, list[dict[str, Any]]]:
    """Generate reproducible multi-scenario benchmark telemetry with ground-truth fault intervals."""
    generator = DeterministicTelemetryGenerator(seed=42)
    start_time = datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC)

    all_metrics = []
    all_logs = []
    all_spans = []

    # 1. 200 normal transactions
    b1 = generator.generate_normal_traffic(count=200, start_time=start_time, interval_seconds=2.0)
    all_metrics.extend(b1.metrics)
    all_logs.extend(b1.logs)
    all_spans.extend(b1.traces)

    # 2. Fault 1: latency_spike (60 txs)
    f1_start = start_time + timedelta(seconds=450)
    b2 = generator.generate_fault_traffic(
        fault_type="latency_spike",
        experiment_id="exp-latency-001",
        count=60,
        start_time=f1_start,
        interval_seconds=2.0,
        custom_params={"latency_ms": 650.0},
    )
    f1_end = f1_start + timedelta(seconds=120)
    all_metrics.extend(b2.metrics)
    all_logs.extend(b2.logs)
    all_spans.extend(b2.traces)

    # 3. Recovery: 80 normal transactions
    rec1_start = f1_end + timedelta(seconds=40)
    b3 = generator.generate_normal_traffic(count=80, start_time=rec1_start, interval_seconds=2.0)
    all_metrics.extend(b3.metrics)
    all_logs.extend(b3.logs)
    all_spans.extend(b3.traces)

    # 4. Fault 2: error_surge (60 txs)
    f2_start = rec1_start + timedelta(seconds=200)
    b4 = generator.generate_fault_traffic(
        fault_type="error_surge",
        experiment_id="exp-error-002",
        count=60,
        start_time=f2_start,
        interval_seconds=2.0,
        custom_params={"error_rate": 0.65},
    )
    f2_end = f2_start + timedelta(seconds=120)
    all_metrics.extend(b4.metrics)
    all_logs.extend(b4.logs)
    all_spans.extend(b4.traces)

    # 5. Fault 3: service_timeout (60 txs)
    f3_start = f2_end + timedelta(seconds=60)
    b5 = generator.generate_fault_traffic(
        fault_type="service_timeout",
        experiment_id="exp-timeout-003",
        count=60,
        start_time=f3_start,
        interval_seconds=2.0,
        custom_params={"timeout_ms": 4000.0},
    )
    f3_end = f3_start + timedelta(seconds=120)
    all_metrics.extend(b5.metrics)
    all_logs.extend(b5.logs)
    all_spans.extend(b5.traces)

    # 6. Post recovery (80 normal transactions)
    rec2_start = f3_end + timedelta(seconds=40)
    b6 = generator.generate_normal_traffic(count=80, start_time=rec2_start, interval_seconds=2.0)
    all_metrics.extend(b6.metrics)
    all_logs.extend(b6.logs)
    all_spans.extend(b6.traces)

    fault_intervals = [
        {
            "experiment_id": "exp-latency-001",
            "service_name": "payment-service",
            "target_service": "payment-service",
            "start_time": f1_start,
            "end_time": f1_end,
            "scenario": "latency_spike",
        },
        {
            "experiment_id": "exp-error-002",
            "service_name": "payment-service",
            "target_service": "payment-service",
            "start_time": f2_start,
            "end_time": f2_end,
            "scenario": "error_surge",
        },
        {
            "experiment_id": "exp-timeout-003",
            "service_name": "payment-service",
            "target_service": "payment-service",
            "start_time": f3_start,
            "end_time": f3_end,
            "scenario": "service_timeout",
        },
    ]

    return all_metrics, all_logs, all_spans, fault_intervals


def extract_telemetry_from_db(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    lookback_hours: float = 24.0,
    limit: int = 100000,
    session_factory: sessionmaker | None = None,
) -> tuple[list, list, list, list[dict[str, Any]], dict[str, Any]]:
    """Query bounded telemetry and experiment ground truth from PostgreSQL telemetry tables.

    Validates database connectivity and telemetry readiness. Never silently falls back.
    Raises DatabaseTrainingError on connection failure or insufficient telemetry.
    """
    # 1. Verify database connectivity
    is_healthy, msg, diag = check_database_connection(timeout_seconds=5.0)
    if not is_healthy:
        raise DatabaseTrainingError(
            f"Database connectivity failed: {msg}. Diagnostic: {diag}. "
            "Actionable remedy: Verify DATABASE_URL environment variable and ensure PostgreSQL service is running and accessible."
        )

    # 2. Compute bounded time window
    now = datetime.now(UTC)
    if end_time is None:
        end_time_utc = now
    else:
        end_time_utc = _to_utc(end_time)

    if start_time is None:
        start_time_utc = end_time_utc - timedelta(hours=lookback_hours)
    else:
        start_time_utc = _to_utc(start_time)

    if start_time_utc >= end_time_utc:
        raise DatabaseTrainingError(
            f"Invalid time bounds: start_time ({start_time_utc.isoformat()}) must precede end_time ({end_time_utc.isoformat()})."
        )

    # 3. Retrieve telemetry and ground-truth experiments
    if session_factory is None:
        session_factory = get_session_factory()

    with session_factory() as session:
        # Bounded Metric records
        metric_stmt = (
            select(MetricRecordModel)
            .where(
                MetricRecordModel.timestamp >= start_time_utc,
                MetricRecordModel.timestamp <= end_time_utc,
            )
            .order_by(MetricRecordModel.timestamp.asc())
            .limit(limit)
        )
        metrics = list(session.execute(metric_stmt).scalars().all())

        # Bounded Log records
        log_stmt = (
            select(LogRecordModel)
            .where(
                LogRecordModel.timestamp >= start_time_utc,
                LogRecordModel.timestamp <= end_time_utc,
            )
            .order_by(LogRecordModel.timestamp.asc())
            .limit(limit)
        )
        logs = list(session.execute(log_stmt).scalars().all())

        # Bounded Trace spans
        span_stmt = (
            select(TraceSpanModel)
            .where(
                TraceSpanModel.start_time >= start_time_utc,
                TraceSpanModel.start_time <= end_time_utc,
            )
            .order_by(TraceSpanModel.start_time.asc())
            .limit(limit)
        )
        spans = list(session.execute(span_stmt).scalars().all())

        # Bounded Experiments
        exp_stmt = (
            select(ExperimentModel)
            .where(
                ExperimentModel.start_time >= start_time_utc,
                ExperimentModel.start_time <= end_time_utc,
            )
            .order_by(ExperimentModel.start_time.asc())
            .limit(limit)
        )
        experiments = list(session.execute(exp_stmt).scalars().all())

        # Bounded Fault labels
        label_stmt = (
            select(FaultLabelModel)
            .where(
                FaultLabelModel.timestamp >= start_time_utc,
                FaultLabelModel.timestamp <= end_time_utc,
            )
            .order_by(FaultLabelModel.timestamp.asc())
            .limit(limit)
        )
        fault_labels = list(session.execute(label_stmt).scalars().all())

    # 4. Enforce strict data readiness validation
    min_required_metrics = 50
    min_required_spans = 10
    if len(metrics) < min_required_metrics or len(spans) < min_required_spans:
        raise DatabaseTrainingError(
            f"Insufficient database telemetry for ML training: Retrieved {len(metrics)} metrics (min {min_required_metrics}) "
            f"and {len(spans)} spans (min {min_required_spans}) in bounded interval [{start_time_utc.isoformat()} -> {end_time_utc.isoformat()}]. "
            "Actionable remedy: Ingest live telemetry via /api/v1/telemetry or run controlled fault experiments before running database training."
        )

    # 5. Build structured fault intervals
    fault_intervals: list[dict[str, Any]] = []
    for exp in experiments:
        st_exp = _to_utc(exp.start_time)
        et_exp = _to_utc(exp.end_time) if exp.end_time else st_exp + timedelta(seconds=120)
        fault_intervals.append(
            {
                "experiment_id": exp.id,
                "target_service": exp.target_service,
                "service_name": exp.target_service,
                "start_time": st_exp,
                "end_time": et_exp,
                "scenario": exp.scenario_name or exp.fault_type,
                "initiator": exp.initiating_service,
            }
        )

    # If experiments are absent but fault labels exist, group labels by experiment
    if not fault_intervals and fault_labels:
        labels_by_exp: dict[str, list[FaultLabelModel]] = {}
        for fl in fault_labels:
            labels_by_exp.setdefault(fl.experiment_id, []).append(fl)
        for exp_id, lbl_list in labels_by_exp.items():
            st_fl = min(_to_utc(lbl.timestamp) for lbl in lbl_list)
            et_fl = max(_to_utc(lbl.timestamp) for lbl in lbl_list)
            if et_fl == st_fl:
                et_fl = st_fl + timedelta(seconds=60)
            fault_intervals.append(
                {
                    "experiment_id": exp_id,
                    "target_service": lbl_list[0].service_name,
                    "service_name": lbl_list[0].service_name,
                    "start_time": st_fl,
                    "end_time": et_fl,
                    "scenario": lbl_list[0].fault_type,
                    "initiator": lbl_list[0].service_name,
                }
            )

    meta = {
        "dataset_origin": "postgresql_telemetry_lake",
        "record_counts": {
            "metrics": len(metrics),
            "logs": len(logs),
            "spans": len(spans),
            "experiments": len(experiments),
            "fault_labels": len(fault_labels),
        },
        "query_bounds": {
            "start_time": start_time_utc.isoformat(),
            "end_time": end_time_utc.isoformat(),
        },
        "experiment_ids": [f["experiment_id"] for f in fault_intervals],
    }

    return metrics, logs, spans, fault_intervals, meta


def check_production_artifact_safeguard(
    artifacts_dir: Path,
    force_overwrite: bool,
) -> None:
    """Ensure experimental runs cannot overwrite production-certified model artifacts."""
    for model_filename in ("isolation_forest.joblib", "incident_predictor.joblib"):
        path = artifacts_dir / model_filename
        if path.exists():
            try:
                import joblib

                artifact = joblib.load(path)
                meta = getattr(artifact, "metadata", {}) or {}
                status = meta.get("status", "")
                if (
                    status in ("PRODUCTION", "PRODUCTION_CERTIFIED", "STAGE")
                    and not force_overwrite
                ):
                    raise DatabaseTrainingError(
                        f"Production Safeguard Violation: Existing artifact '{path}' is designated as '{status}'. "
                        "Refusing to overwrite production-certified model with an experimental training run. "
                        "Pass --force-overwrite-production if you explicitly intend to replace it."
                    )
            except DatabaseTrainingError:
                raise
            except Exception:
                pass


def run_training_pipeline(
    data_source: str = "synthetic",
    start_time: str | None = None,
    end_time: str | None = None,
    lookback_hours: float = 24.0,
    limit: int = 100000,
    force_overwrite_production: bool = False,
    session_factory: sessionmaker | None = None,
) -> int:
    """Execute complete end-to-end model training, evaluation, and serialization."""
    print("=================================================================")
    print("  AegisOps — Phase 3: ML Pipeline Training & Evaluation CLI      ")
    print("=================================================================")
    print(
        f"Configuration: data_source='{data_source}', force_overwrite_production={force_overwrite_production}"
    )

    artifacts_dir = Path("ml/artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Enforce production safeguard
    try:
        check_production_artifact_safeguard(
            artifacts_dir, force_overwrite=force_overwrite_production
        )
    except DatabaseTrainingError as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 1

    # Step 1: Telemetry Ingestion based on explicit data source
    print(f"\n[Step 1/5] Extracting telemetry dataset (source: {data_source})...")
    data_meta: dict[str, Any] = {}

    if data_source == "db":
        st_dt = datetime.fromisoformat(start_time) if start_time else None
        et_dt = datetime.fromisoformat(end_time) if end_time else None
        try:
            metrics, logs, spans, fault_intervals, data_meta = extract_telemetry_from_db(
                start_time=st_dt,
                end_time=et_dt,
                lookback_hours=lookback_hours,
                limit=limit,
                session_factory=session_factory,
            )
        except DatabaseTrainingError as exc:
            print(f"\n[ERROR] Database mode failed: {exc}", file=sys.stderr)
            return 1

        dataset_origin = "postgresql_telemetry_lake"
        dataset_name = "postgresql_bounded_telemetry"
        model_status = "EXPERIMENTAL"
        production_validation_status = "INCOMPLETE (Telemetry span below production graduation threshold: minimum 7 days and 20 incident events required)"
        print(
            f"Successfully retrieved from PostgreSQL: {len(metrics)} metrics, {len(logs)} logs, {len(spans)} spans, "
            f"{len(fault_intervals)} fault experiments."
        )
    elif data_source == "synthetic":
        metrics, logs, spans, fault_intervals = generate_benchmark_telemetry_dataset()
        dataset_origin = "deterministic_fixture_generator"
        dataset_name = "synthetic_benchmark_multi_scenario"
        model_status = "EXPERIMENTAL"
        production_validation_status = (
            "INCOMPLETE (Trained on synthetic fixtures; not production-validated)"
        )
        data_meta = {
            "dataset_origin": dataset_origin,
            "record_counts": {
                "metrics": len(metrics),
                "logs": len(logs),
                "spans": len(spans),
                "experiments": len(fault_intervals),
            },
            "experiment_ids": [f["experiment_id"] for f in fault_intervals],
        }
        print(
            f"Generated benchmark fixtures: {len(metrics)} metric points, {len(logs)} log records, {len(spans)} trace spans."
        )
    else:
        print(
            f"[ERROR] Unsupported data-source '{data_source}'. Must be 'db' or 'synthetic'.",
            file=sys.stderr,
        )
        return 1

    # Determine timeline boundaries
    all_ts = [m.timestamp for m in metrics]
    min_ts = _to_utc(min(all_ts))
    max_ts = _to_utc(max(all_ts))
    span_seconds = (max_ts - min_ts).total_seconds()
    print(
        f"Telemetry span: {min_ts.isoformat()} -> {max_ts.isoformat()} (duration: {span_seconds:.0f}s)"
    )

    # Step 2: Build sliding-window operational feature vectors
    print("\n[Step 2/5] Building sliding-window operational feature vectors...")
    service_names = sorted({m.service_name for m in metrics}.union({s.service_name for s in spans}))
    if not service_names:
        service_names = ["payment-service", "order-service"]

    feature_df = build_sliding_feature_dataset(
        metrics=metrics,
        logs=logs,
        spans=spans,
        services=service_names,
        window_seconds=60.0,
        step_seconds=20.0,
    )
    print(
        f"Constructed {len(feature_df)} feature vectors across {len(service_names)} services: {service_names}"
    )

    if feature_df.empty:
        print(
            "[ERROR] No feature vectors could be constructed from telemetry. Telemetry window too narrow.",
            file=sys.stderr,
        )
        return 1

    # Attach ground-truth anomaly annotations and forward-looking incident targets
    labeled_df = attach_incident_labels(
        df=feature_df,
        experiments=fault_intervals,
        horizon_seconds=300.0,
        embargo_seconds=60.0,
    )

    n_incident_samples = int(labeled_df["is_incident"].sum())
    n_target_samples = int((labeled_df["target_incident_in_horizon"] == 1).sum())
    n_embargoed_total = int(labeled_df["is_embargoed"].sum())
    print(
        f"Labeled samples: {len(labeled_df)} total | {n_incident_samples} active anomalies | "
        f"{n_target_samples} future incident targets | {n_embargoed_total} embargoed/ambiguous samples"
    )

    # Chronological Split (65% train, 10% embargo buffer, 25% test)
    labeled_df = labeled_df.sort_values("timestamp").reset_index(drop=True)
    n_total = len(labeled_df)
    train_end_idx = int(n_total * 0.65)
    test_start_idx = int(n_total * 0.75)  # 10% chronological embargo gap between train and test

    train_df = labeled_df.iloc[:train_end_idx].copy()
    embargo_df = labeled_df.iloc[train_end_idx:test_start_idx].copy()
    test_df = labeled_df.iloc[test_start_idx:].copy()

    split_meta = {
        "total_samples": n_total,
        "train_samples": len(train_df),
        "embargo_gap_samples": len(embargo_df),
        "test_samples": len(test_df),
        "train_start": train_df["timestamp"].min().isoformat(),
        "train_end": train_df["timestamp"].max().isoformat(),
        "test_start": test_df["timestamp"].min().isoformat(),
        "test_end": test_df["timestamp"].max().isoformat(),
    }
    print(
        f"Chronological split: Train={len(train_df)} ({split_meta['train_start'][:19]} - {split_meta['train_end'][:19]}), "
        f"Embargo Gap={len(embargo_df)}, Test={len(test_df)} ({split_meta['test_start'][:19]} - {split_meta['test_end'][:19]})"
    )

    # Step 3: Fitting ML Models
    print("\n[Step 3/5] Fitting ML Models...")

    # A. Isolation Forest on nominal training data
    train_nominal_df = train_df[train_df["is_incident"] == 0].copy()
    if len(train_nominal_df) < 20:
        train_nominal_df = train_df.copy()  # Fallback if nominal partition is small

    print(f"Fitting Isolation Forest on {len(train_nominal_df)} nominal training samples...")
    iforest = IsolationForestDetector(
        model_version="v1.0.0",
        contamination=0.08,
        random_state=42,
        alert_threshold=0.60,
    )
    iforest.fit(
        df=train_nominal_df[STANDARD_FEATURE_NAMES],
        training_metadata={
            "dataset": dataset_name,
            "dataset_origin": dataset_origin,
            "is_production_validated": False,
            "production_validation_status": production_validation_status,
            "split": split_meta,
            "record_counts": data_meta.get("record_counts", {}),
            "status": model_status,
        },
    )
    iforest_path = artifacts_dir / "isolation_forest.joblib"
    iforest.save(iforest_path)
    print(f"Saved Isolation Forest artifact to: {iforest_path}")

    # B. Supervised Incident Risk Predictor (Finding 2: Filter out embargoed samples)
    train_unambiguous_df = train_df[~train_df["is_embargoed"]].copy()
    pos_train_count = int((train_unambiguous_df["target_incident_in_horizon"] == 1).sum())
    neg_train_count = int((train_unambiguous_df["target_incident_in_horizon"] == 0).sum())
    n_train_embargo_excluded = len(train_df) - len(train_unambiguous_df)
    print(
        f"Fitting Supervised Incident Risk Predictor on {len(train_unambiguous_df)} unambiguous training samples "
        f"(Excluded {n_train_embargo_excluded} embargoed rows | {pos_train_count} positive targets | {neg_train_count} negative targets)..."
    )

    incident_predictor = IncidentRiskPredictor(
        model_version="v1.0.0",
        horizon_seconds=300.0,
        risk_threshold=0.50,
        min_positive_samples=5,
        random_state=42,
    )

    try:
        incident_predictor.fit(
            X_train=train_unambiguous_df[STANDARD_FEATURE_NAMES],
            y_train=train_unambiguous_df["target_incident_in_horizon"],
            training_metadata={
                "dataset": dataset_name,
                "dataset_origin": dataset_origin,
                "is_production_validated": False,
                "production_validation_status": production_validation_status,
                "split": split_meta,
                "record_counts": data_meta.get("record_counts", {}),
                "status": model_status,
            },
        )
        incident_path = artifacts_dir / "incident_predictor.joblib"
        incident_predictor.save(incident_path)
        print(f"Saved Incident Risk Predictor artifact to: {incident_path}")
    except ValueError as exc:
        print(f"Incident predictor training suspended: {exc}")
        incident_predictor = None

    # Step 4: Held-out Chronological Evaluation with Detection Delay & Embargo Filtering
    print("\n[Step 4/5] Executing Rigorous Held-out Chronological Evaluation...")
    threshold_detector = ThresholdAnomalyDetector()
    rolling_mad_detector = RollingMadAnomalyDetector(z_threshold=3.5, history_window=30)

    # Filter test fault intervals that overlap test timeline
    test_start_ts = _to_utc(pd.to_datetime(split_meta["test_start"]).to_pydatetime())
    test_end_ts = _to_utc(pd.to_datetime(split_meta["test_end"]).to_pydatetime())
    eval_fault_intervals = [
        f
        for f in fault_intervals
        if f["end_time"] >= test_start_ts and f["start_time"] <= test_end_ts
    ]
    if not eval_fault_intervals:
        eval_fault_intervals = fault_intervals  # Fallback to full list if none strictly in range

    eval_report = generate_full_evaluation_report(
        threshold_detector=threshold_detector,
        rolling_mad_detector=rolling_mad_detector,
        isolation_forest_detector=iforest,
        incident_predictor=incident_predictor,
        test_df=test_df,
        ground_truth_labels=test_df["is_incident"],
        incident_target_labels=test_df["target_incident_in_horizon"],
        output_path=artifacts_dir / "evaluation_report.json",
        fault_intervals=eval_fault_intervals,
        evaluation_metadata={
            "dataset_origin": dataset_origin,
            "data_source_mode": data_source,
            "is_production_validated": False,
            "production_validation_status": production_validation_status,
            "chronological_split": split_meta,
            "record_counts": data_meta.get("record_counts", {}),
            "experiment_ids": [f.get("experiment_id", f.get("scenario")) for f in fault_intervals],
            "evaluation_date": datetime.now(UTC).isoformat(),
        },
    )

    print("\n--- Model Benchmark Results (Held-Out Test Partition) ---")
    for name, res in eval_report["anomaly_detectors"].items():
        print(f"Detector [{name}]:")
        print(
            f"  Precision: {res['precision']:.2%} | Recall: {res['recall']:.2%} | F1-Score: {res['f1_score']:.2%}"
        )
        print(
            f"  PR-AUC: {res.get('pr_auc', 0.0):.3f} | False Alerts/hr: {res['false_alerts_per_hour']} | Missed Incidents: {res['missed_incidents']}"
        )
        if "detection_delay" in res and res["detection_delay"]:
            dd = res["detection_delay"]
            summary = dd.get("summary", {})
            mean_str = (
                f"{summary['mean_seconds']}s" if summary.get("mean_seconds") is not None else "N/A"
            )
            median_str = (
                f"{summary['median_seconds']}s"
                if summary.get("median_seconds") is not None
                else "N/A"
            )
            print(
                f"  Detection Delay: Mean={mean_str} | Median={median_str} | "
                f"Detection Rate={dd.get('detection_rate', 0.0):.1%} ({dd.get('detected_incidents')}/{dd.get('total_incidents')})"
            )

    if "incident_prediction" in eval_report and eval_report["incident_prediction"]:
        ip_res = eval_report["incident_prediction"]
        print(f"Predictor [{ip_res.get('model_name')}]:")
        print(f"  Status: {ip_res.get('status')}")
        if ip_res.get("status") == "AVAILABLE":
            print(
                f"  Precision: {ip_res['precision']:.2%} | Recall: {ip_res['recall']:.2%} | F1-Score: {ip_res['f1_score']:.2%}"
            )
            print(
                f"  PR-AUC: {ip_res['pr_auc']:.3f} | Brier Calibration: {ip_res['brier_score']:.3f}"
            )
            print(
                f"  Recall @ Top 20% Alert Budget: {ip_res['recall_at_top20_percent_alerts']:.2%}"
            )
            print(f"  Embargoed Samples Excluded: {ip_res.get('embargoed_samples_excluded', 0)}")

    print(
        f"\nSaved machine-readable evaluation report to: {artifacts_dir / 'evaluation_report.json'}"
    )

    # Step 5: Registering Models in Database Registry
    print("\n[Step 5/5] Registering Models in Database Registry...")
    try:
        sf = session_factory or get_session_factory()
        with sf() as db:
            # Register Isolation Forest
            db.merge(
                MLModelModel(
                    id="mdl-isolation-forest",
                    model_name="isolation_forest",
                    version="v1.0.0",
                    model_type="unsupervised_isolation_forest",
                    status=model_status,
                    trained_at=datetime.now(UTC),
                    artifact_path=str(iforest_path),
                    feature_names=list(STANDARD_FEATURE_NAMES),
                    metrics_json=eval_report["anomaly_detectors"].get("isolation_forest", {}),
                    hyperparameters={
                        "contamination": iforest.contamination,
                        "random_state": iforest.random_state,
                        "alert_threshold": iforest.alert_threshold,
                        "dataset_origin": dataset_origin,
                    },
                )
            )

            # Register Incident Risk Predictor
            if incident_predictor is not None and incident_predictor.is_fitted:
                incident_path = artifacts_dir / "incident_predictor.joblib"
                db.merge(
                    MLModelModel(
                        id="mdl-incident-risk-predictor",
                        model_name="incident_risk_predictor",
                        version="v1.0.0",
                        model_type="supervised_random_forest",
                        status=model_status,
                        trained_at=datetime.now(UTC),
                        artifact_path=str(incident_path),
                        feature_names=list(STANDARD_FEATURE_NAMES),
                        metrics_json=eval_report.get("incident_prediction", {}),
                        hyperparameters={
                            "horizon_seconds": incident_predictor.horizon_seconds,
                            "risk_threshold": incident_predictor.risk_threshold,
                            "random_state": incident_predictor.random_state,
                            "dataset_origin": dataset_origin,
                        },
                    )
                )

            # Register Baselines
            db.merge(
                MLModelModel(
                    id="mdl-threshold-baseline",
                    model_name="threshold_baseline",
                    version="v1.0.0",
                    model_type="deterministic_rule",
                    status="AVAILABLE",
                    trained_at=None,
                    artifact_path=None,
                    feature_names=list(STANDARD_FEATURE_NAMES),
                    metrics_json=eval_report["anomaly_detectors"].get("threshold_baseline", {}),
                    hyperparameters={"error_rate_threshold": 0.10, "latency_p95_ms": 500.0},
                )
            )

            db.merge(
                MLModelModel(
                    id="mdl-rolling-mad-baseline",
                    model_name="rolling_mad_baseline",
                    version="v1.0.0",
                    model_type="statistical_robust",
                    status="AVAILABLE",
                    trained_at=None,
                    artifact_path=None,
                    feature_names=list(STANDARD_FEATURE_NAMES),
                    metrics_json=eval_report["anomaly_detectors"].get("rolling_mad_baseline", {}),
                    hyperparameters={"history_window": 30, "z_threshold": 3.5},
                )
            )

            db.commit()
            print("Successfully registered models in PostgreSQL ml_models registry.")
    except Exception as exc:
        print(
            f"Warning: Database registry update skipped ({exc}). Artifacts are preserved on disk."
        )

    print("\nPhase 3 ML Training Pipeline completed successfully!")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AegisOps — Phase 3: ML Pipeline Training & Evaluation CLI"
    )
    parser.add_argument(
        "--data-source",
        choices=["db", "synthetic"],
        default="synthetic",
        help="Telemetry data source: 'db' (PostgreSQL lake) or 'synthetic' (deterministic benchmark).",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        default=None,
        help="Bounded query start timestamp in ISO-8601 format (e.g. 2026-10-10T00:00:00Z).",
    )
    parser.add_argument(
        "--end-time",
        type=str,
        default=None,
        help="Bounded query end timestamp in ISO-8601 format (e.g. 2026-10-10T01:00:00Z).",
    )
    parser.add_argument(
        "--lookback-hours",
        type=float,
        default=24.0,
        help="Hours to look back from end-time if start-time is omitted (default: 24.0).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100000,
        help="Maximum records to query per telemetry table (default: 100000).",
    )
    parser.add_argument(
        "--force-overwrite-production",
        action="store_true",
        default=False,
        help="Safeguard bypass: Allow experimental runs to overwrite production-certified artifacts.",
    )
    cli_args = parser.parse_args()

    exit_code = run_training_pipeline(
        data_source=cli_args.data_source,
        start_time=cli_args.start_time,
        end_time=cli_args.end_time,
        lookback_hours=cli_args.lookback_hours,
        limit=cli_args.limit,
        force_overwrite_production=cli_args.force_overwrite_production,
    )
    sys.exit(exit_code)
