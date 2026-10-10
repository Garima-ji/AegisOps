from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.telemetry import LogRecordModel, MetricRecordModel, TraceSpanModel
from app.schemas.telemetry import LogRecordSchema, MetricPoint, SpanRecordSchema

# Canonical ordered feature list used across all ML models and inference schemas
STANDARD_FEATURE_NAMES: list[str] = [
    "req_count",
    "req_rate_per_sec",
    "error_count",
    "error_rate",
    "latency_mean_ms",
    "latency_median_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "latency_std_ms",
    "cpu_utilization_mean",
    "memory_utilization_mean",
    "child_span_count",
    "error_span_count",
    "missingness_ratio",
    "freshness_lag_seconds",
]

EXPECTED_METRIC_NAMES: frozenset[str] = frozenset(
    {
        "http.server.request.duration_ms",
        "http.server.requests.count",
        "http.server.errors.count",
        "system.cpu.utilization",
        "system.memory.utilization",
    }
)


def _to_utc(dt: datetime) -> datetime:
    """Ensure timestamp is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def extract_window_features(
    service_name: str,
    window_end: datetime,
    window_seconds: float = 60.0,
    metrics: Sequence[MetricPoint | MetricRecordModel] | None = None,
    logs: Sequence[LogRecordSchema | LogRecordModel] | None = None,
    spans: Sequence[SpanRecordSchema | TraceSpanModel] | None = None,
) -> dict[str, float]:
    """Extract leak-free operational feature vector for a service over [window_end - window_seconds, window_end].

    STRICT GUARANTEE: Any observation with timestamp > window_end is strictly ignored.
    """
    cutoff = _to_utc(window_end)
    window_start = cutoff - timedelta(seconds=window_seconds)

    metrics = metrics or []
    logs = logs or []
    spans = spans or []

    # 1. Filter observations strictly within (window_start, window_end] for the target service
    window_metrics = [
        m
        for m in metrics
        if m.service_name == service_name and window_start < _to_utc(m.timestamp) <= cutoff
    ]

    window_logs = [
        lg
        for lg in logs
        if lg.service_name == service_name and window_start < _to_utc(lg.timestamp) <= cutoff
    ]

    window_spans = [
        sp
        for sp in spans
        if sp.service_name == service_name and window_start < _to_utc(sp.start_time) <= cutoff
    ]

    # Downstream dependency spans (where target service called something else or was called)
    all_trace_ids = {sp.trace_id for sp in window_spans}
    downstream_spans = [
        sp
        for sp in spans
        if sp.trace_id in all_trace_ids
        and sp.service_name != service_name
        and window_start < _to_utc(sp.start_time) <= cutoff
    ]

    # 2. Extract Latency & Request Statistics
    latency_values: list[float] = []
    req_counts: list[float] = []
    error_counts: list[float] = []
    cpu_values: list[float] = []
    mem_values: list[float] = []
    observed_metric_names: set[str] = set()
    latest_observed_ts: datetime | None = None

    for m in window_metrics:
        ts = _to_utc(m.timestamp)
        if latest_observed_ts is None or ts > latest_observed_ts:
            latest_observed_ts = ts

        observed_metric_names.add(m.metric_name)

        if m.metric_name == "http.server.request.duration_ms":
            latency_values.append(float(m.value))
        elif m.metric_name == "http.server.requests.count":
            req_counts.append(float(m.value))
        elif m.metric_name == "http.server.errors.count":
            error_counts.append(float(m.value))
        elif m.metric_name == "system.cpu.utilization":
            cpu_values.append(float(m.value))
        elif m.metric_name == "system.memory.utilization":
            mem_values.append(float(m.value))

    # Also augment latencies from trace spans if direct metric is missing
    if not latency_values and window_spans:
        for sp in window_spans:
            dur = sp.duration_ms if sp.duration_ms is not None else 0.0
            latency_values.append(float(dur))

    # Also augment error counts from logs with ERROR / CRITICAL severity
    log_errors = sum(1 for lg in window_logs if str(lg.severity).upper() in {"ERROR", "CRITICAL"})

    total_req_count = sum(req_counts) if req_counts else float(len(window_spans))
    total_error_count = sum(error_counts) + log_errors

    # If spans had errors, record them
    error_span_count = float(
        sum(1 for sp in window_spans if str(sp.status_code).upper() == "ERROR")
    )
    child_span_count = float(len(downstream_spans))

    # Compute Latency Percentiles
    if latency_values:
        arr = np.array(latency_values, dtype=np.float64)
        lat_mean = float(np.mean(arr))
        lat_median = float(np.median(arr))
        lat_p95 = float(np.percentile(arr, 95))
        lat_p99 = float(np.percentile(arr, 99))
        lat_std = float(np.std(arr)) if len(arr) > 1 else 0.0
    else:
        lat_mean = 0.0
        lat_median = 0.0
        lat_p95 = 0.0
        lat_p99 = 0.0
        lat_std = 0.0

    # Rates
    req_rate = round(total_req_count / max(1.0, window_seconds), 3)
    error_rate = round(total_error_count / max(1.0, total_req_count), 4)

    # CPU & Memory
    cpu_mean = float(np.mean(cpu_values)) if cpu_values else 0.0
    mem_mean = float(np.mean(mem_values)) if mem_values else 0.0

    # Freshness & Missingness
    expected_count = len(EXPECTED_METRIC_NAMES)
    missing_count = expected_count - len(observed_metric_names.intersection(EXPECTED_METRIC_NAMES))
    missingness_ratio = round(missing_count / expected_count, 3)

    if latest_observed_ts is not None:
        freshness_lag = max(0.0, round((cutoff - latest_observed_ts).total_seconds(), 2))
    else:
        freshness_lag = float(window_seconds)

    return {
        "req_count": float(total_req_count),
        "req_rate_per_sec": req_rate,
        "error_count": float(total_error_count),
        "error_rate": error_rate,
        "latency_mean_ms": round(lat_mean, 2),
        "latency_median_ms": round(lat_median, 2),
        "latency_p95_ms": round(lat_p95, 2),
        "latency_p99_ms": round(lat_p99, 2),
        "latency_std_ms": round(lat_std, 2),
        "cpu_utilization_mean": round(cpu_mean, 2),
        "memory_utilization_mean": round(mem_mean, 2),
        "child_span_count": child_span_count,
        "error_span_count": error_span_count,
        "missingness_ratio": missingness_ratio,
        "freshness_lag_seconds": freshness_lag,
    }


def extract_features_from_db(
    session: Session,
    service_name: str,
    window_end: datetime,
    window_seconds: float = 60.0,
) -> dict[str, float]:
    """Query database lake and extract feature vector for window ending at window_end."""
    cutoff = _to_utc(window_end)
    window_start = cutoff - timedelta(seconds=window_seconds)

    metric_stmt = select(MetricRecordModel).where(
        MetricRecordModel.service_name == service_name,
        MetricRecordModel.timestamp > window_start,
        MetricRecordModel.timestamp <= cutoff,
    )
    metrics = list(session.execute(metric_stmt).scalars().all())

    log_stmt = select(LogRecordModel).where(
        LogRecordModel.service_name == service_name,
        LogRecordModel.timestamp > window_start,
        LogRecordModel.timestamp <= cutoff,
    )
    logs = list(session.execute(log_stmt).scalars().all())

    span_stmt = select(TraceSpanModel).where(
        TraceSpanModel.start_time > window_start,
        TraceSpanModel.start_time <= cutoff,
    )
    spans = list(session.execute(span_stmt).scalars().all())

    return extract_window_features(
        service_name=service_name,
        window_end=cutoff,
        window_seconds=window_seconds,
        metrics=metrics,
        logs=logs,
        spans=spans,
    )


def build_sliding_feature_dataset(
    metrics: list[MetricPoint | MetricRecordModel],
    logs: list[LogRecordSchema | LogRecordModel],
    spans: list[SpanRecordSchema | TraceSpanModel],
    services: list[str] | None = None,
    window_seconds: float = 60.0,
    step_seconds: float = 10.0,
) -> pd.DataFrame:
    """Build a time-series feature matrix using sliding windows across telemetry."""
    if not metrics and not spans:
        return pd.DataFrame(columns=["timestamp", "service_name", *STANDARD_FEATURE_NAMES])

    all_timestamps: list[datetime] = []
    for m in metrics:
        all_timestamps.append(_to_utc(m.timestamp))
    for s in spans:
        all_timestamps.append(_to_utc(s.start_time))

    min_ts = min(all_timestamps)
    max_ts = max(all_timestamps)

    if services is None:
        svc_set: set[str] = {m.service_name for m in metrics}.union({s.service_name for s in spans})
        services = sorted(svc_set)

    rows: list[dict[str, Any]] = []
    # Start sliding windows once at least one window_seconds has elapsed
    cur_end = min_ts + timedelta(seconds=window_seconds)

    while cur_end <= max_ts:
        for svc in services:
            feats = extract_window_features(
                service_name=svc,
                window_end=cur_end,
                window_seconds=window_seconds,
                metrics=metrics,
                logs=logs,
                spans=spans,
            )
            row: dict[str, Any] = {
                "timestamp": cur_end,
                "service_name": svc,
                **feats,
            }
            rows.append(row)
        cur_end += timedelta(seconds=step_seconds)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "service_name", *STANDARD_FEATURE_NAMES])
    return df


def attach_incident_labels(
    df: pd.DataFrame,
    experiments: list[dict[str, Any]],
    horizon_seconds: float = 300.0,
    embargo_seconds: float = 30.0,
) -> pd.DataFrame:
    """Attach leak-free binary incident labels to sliding window feature samples.

    Label target:
      1 if an incident for service_name begins in (timestamp, timestamp + horizon_seconds]
      0 if no incident begins in that horizon
    Samples during active faults or in the embargo buffer are tagged as is_embargoed=True.
    """
    if df.empty:
        df["target"] = pd.Series(dtype=int)
        df["is_embargoed"] = pd.Series(dtype=bool)
        return df

    parsed_exps = []
    for exp in experiments:
        st_raw = exp["start_time"]
        et_raw = exp["end_time"]
        st = _to_utc(st_raw if isinstance(st_raw, datetime) else datetime.fromisoformat(st_raw))
        et = _to_utc(et_raw if isinstance(et_raw, datetime) else datetime.fromisoformat(et_raw))
        svc_name = exp.get("target_service") or exp.get("service_name", "")
        parsed_exps.append(
            {
                "target_service": svc_name,
                "start_time": st,
                "end_time": et,
                "scenario": exp.get("scenario", "fault"),
                "initiator": exp.get("initiator", svc_name),
            }
        )

    targets: list[int] = []
    embargoed: list[bool] = []
    is_incidents: list[int] = []
    scenarios: list[str] = []

    for _, row in df.iterrows():
        ts_raw = row["timestamp"]
        ts = _to_utc(
            ts_raw if isinstance(ts_raw, datetime) else pd.to_datetime(ts_raw).to_pydatetime()
        )
        svc = str(row["service_name"])
        horizon_end = ts + timedelta(seconds=horizon_seconds)

        is_emb = False
        is_pos = False
        is_inc = False
        active_scenario = "normal"

        for exp in parsed_exps:
            exp_svc = exp["target_service"]
            st = exp["start_time"]
            et = exp["end_time"]

            # Active incident condition
            if svc == exp_svc and (st <= ts <= et):
                is_inc = True
                active_scenario = exp["scenario"]

            # Embargo condition: active fault duration or within embargo buffer
            if svc == exp_svc and (
                (st - timedelta(seconds=embargo_seconds))
                <= ts
                <= (et + timedelta(seconds=embargo_seconds))
            ):
                is_emb = True

            # Future incident target: starts strictly within (ts, ts + horizon_seconds]
            if svc == exp_svc and (ts < st <= horizon_end):
                is_pos = True

        # Assign unambiguous labels:
        # Ambiguous / embargoed samples must NOT be relabeled as normal (0).
        # We designate them with target = -1 so any un-filtered training attempt is rejected.
        if is_emb:
            target_val = -1
        elif is_pos:
            target_val = 1
        else:
            target_val = 0

        targets.append(target_val)
        embargoed.append(is_emb)
        is_incidents.append(1 if is_inc else 0)
        scenarios.append(active_scenario)

    res = df.copy()
    res["target"] = targets
    res["target_incident_in_horizon"] = targets
    res["is_incident"] = is_incidents
    res["scenario"] = scenarios
    res["is_embargoed"] = embargoed
    return res
