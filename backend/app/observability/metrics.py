"""Operational metrics registry and Prometheus exposition for AegisOps."""

from __future__ import annotations

import re
import threading
import time
from typing import Any

_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Strict Label Cardinality Enforcement
# ---------------------------------------------------------------------------
_UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_ID_REGEX = re.compile(r"^[0-9a-f]{16,64}$", re.I)


def _sanitize_label_value(key: str, val: Any) -> str:
    """Ensure metric label values are strictly low-cardinality to prevent memory explosion."""
    s = str(val).strip()
    if not s:
        return "none"
    # Reject high-cardinality IDs
    if _UUID_REGEX.match(s) or _HEX_ID_REGEX.match(s) or len(s) > 50:
        return "redacted_id"
    # Clean characters
    return re.sub(r"[^a-zA-Z0-9_\-\.]", "_", s)


def _serialize_labels(labels: dict[str, Any] | None) -> str:
    """Format label dictionary into canonical Prometheus label string."""
    if not labels:
        return ""
    sorted_pairs = sorted(
        (k, _sanitize_label_value(k, v))
        for k, v in labels.items()
        if k not in {"trace_id", "incident_id", "investigation_id", "message", "prompt"}
    )
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted_pairs) + "}"


# ---------------------------------------------------------------------------
# In-Memory Metrics State
# ---------------------------------------------------------------------------

_counters: dict[str, int] = {
    # Phase 2 Ingestion
    "telemetry.metrics.received_total": 0,
    "telemetry.metrics.accepted_total": 0,
    "telemetry.metrics.rejected_total": 0,
    "telemetry.logs.received_total": 0,
    "telemetry.logs.accepted_total": 0,
    "telemetry.logs.rejected_total": 0,
    "telemetry.traces.received_total": 0,
    "telemetry.traces.accepted_total": 0,
    "telemetry.traces.rejected_total": 0,
    "telemetry.ingestion.batches_total": 0,
    "telemetry.ingestion.errors_total": 0,
    "telemetry.quality.stale_total": 0,
    "telemetry.quality.missing_total": 0,
    "telemetry.quality.schema_deviations_total": 0,
    "experiments.total": 0,
    "experiments.active": 0,
    # Phase 3 ML
    "ml.anomalies.detected_total": 0,
    "ml.predictions.total": 0,
    "ml.predictions.errors_total": 0,
    "ml.model_load.failures_total": 0,
    "ml.drift.alerts_total": 0,
    # Phase 4 RCA
    "rca.requests.total": 0,
    "rca.abstentions.total": 0,
    # Phase 5 Agent & Remediation
    "agent.investigations.started_total": 0,
    "agent.investigations.completed_total": 0,
    "agent.investigations.abstained_total": 0,
    "agent.tool_calls.total": 0,
    "agent.tool_calls.errors_total": 0,
    "agent.citation_validation.failures_total": 0,
    "agent.remediation.proposed_total": 0,
    "agent.remediation.policy_allow_total": 0,
    "agent.remediation.policy_deny_total": 0,
    "agent.remediation.policy_approval_required_total": 0,
    "agent.remediation.simulations_total": 0,
    "agent.remediation.verification_mismatches_total": 0,
    # Phase 6 System & DB
    "http.requests.total": 0,
    "http.requests.errors_total": 0,
    "db.queries.total": 0,
    "db.connection.errors_total": 0,
}

_labeled_counters: dict[str, dict[str, int]] = {}

_durations: dict[str, list[float]] = {
    "metrics_ingestion_ms": [],
    "logs_ingestion_ms": [],
    "traces_ingestion_ms": [],
    "feature_generation_ms": [],
    "ml_inference_ms": [],
    "rca_duration_ms": [],
    "investigation_duration_ms": [],
    "db_query_ms": [],
    "http_request_ms": [],
}


# ---------------------------------------------------------------------------
# Public Metric Recording Functions
# ---------------------------------------------------------------------------


def increment_counter(key: str, amount: int = 1, labels: dict[str, Any] | None = None) -> None:
    """Safely increment a low-cardinality operational counter."""
    with _lock:
        if labels:
            lbl_str = _serialize_labels(labels)
            if key not in _labeled_counters:
                _labeled_counters[key] = {}
            _labeled_counters[key][lbl_str] = _labeled_counters[key].get(lbl_str, 0) + amount

        # Base counter
        if key in _counters:
            _counters[key] += amount
        else:
            _counters[key] = amount


def record_duration(category: str, duration_ms: float) -> None:
    """Record operation latency sample in bounded circular buffer."""
    with _lock:
        bucket = category if category.endswith("_ms") else f"{category}_ms"
        if bucket not in _durations:
            _durations[bucket] = []
        samples = _durations[bucket]
        samples.append(duration_ms)
        if len(samples) > 500:
            _durations[bucket] = samples[-500:]


def get_observability_metrics() -> dict[str, Any]:
    """Return point-in-time snapshot of operational metrics."""
    with _lock:
        stats: dict[str, Any] = {
            "counters": dict(_counters),
            "labeled_counters": {k: dict(v) for k, v in _labeled_counters.items()},
            "latencies_ms": {},
            "timestamp": time.time(),
        }
        for bucket, samples in _durations.items():
            if samples:
                stats["latencies_ms"][bucket] = {
                    "count": len(samples),
                    "avg": round(sum(samples) / len(samples), 2),
                    "min": round(min(samples), 2),
                    "max": round(max(samples), 2),
                    "p50": round(sorted(samples)[int(len(samples) * 0.50)], 2),
                    "p95": round(
                        sorted(samples)[min(int(len(samples) * 0.95), len(samples) - 1)], 2
                    ),
                }
            else:
                stats["latencies_ms"][bucket] = {
                    "count": 0,
                    "avg": 0.0,
                    "min": 0.0,
                    "max": 0.0,
                    "p50": 0.0,
                    "p95": 0.0,
                }
        return stats


def reset_observability_metrics() -> None:
    """Reset all metric counters and duration buffers for test isolation."""
    with _lock:
        for k in _counters:
            _counters[k] = 0
        _labeled_counters.clear()
        for k in _durations:
            _durations[k] = []


def get_metrics_snapshot() -> dict[str, Any]:
    """Return metric snapshot matching standard API response schema."""
    raw = get_observability_metrics()
    return {
        "counters": raw["counters"],
        "durations_ms": raw["latencies_ms"],
        "active_experiments": raw["counters"].get("experiments.active", 0),
    }


def render_prometheus_text() -> str:
    """Render operational metrics in standard Prometheus text exposition format."""
    with _lock:
        lines: list[str] = [
            "# HELP aegisops_metric_counters General operational counters",
            "# TYPE aegisops_metric_counters counter",
        ]
        # Render base counters
        for metric_name, val in sorted(_counters.items()):
            prom_name = "aegisops_" + metric_name.replace(".", "_")
            lines.append(f"{prom_name} {val}")

        # Render labeled counters
        for metric_name, label_map in sorted(_labeled_counters.items()):
            prom_name = "aegisops_" + metric_name.replace(".", "_")
            for lbl_str, val in sorted(label_map.items()):
                lines.append(f"{prom_name}{lbl_str} {val}")

        # Render duration summaries
        lines.append("\n# HELP aegisops_duration_milliseconds Operation duration statistics")
        lines.append("# TYPE aegisops_duration_milliseconds summary")
        for bucket, samples in sorted(_durations.items()):
            prom_name = "aegisops_" + bucket.replace(".", "_")
            if samples:
                cnt = len(samples)
                s_sum = sum(samples)
                p50 = sorted(samples)[int(cnt * 0.50)]
                p95 = sorted(samples)[min(int(cnt * 0.95), cnt - 1)]
                lines.append(f'{prom_name}{{quantile="0.5"}} {p50:.2f}')
                lines.append(f'{prom_name}{{quantile="0.95"}} {p95:.2f}')
                lines.append(f"{prom_name}_sum {s_sum:.2f}")
                lines.append(f"{prom_name}_count {cnt}")
            else:
                lines.append(f"{prom_name}_sum 0.0")
                lines.append(f"{prom_name}_count 0")

        return "\n".join(lines) + "\n"
