"""Reconstructs directed service dependency topology from distributed trace spans."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.db.models.telemetry import TraceSpanModel
from app.schemas.telemetry import SpanRecordSchema
from app.topology.models import DependencyEdge, TopologyGraph, _to_utc


def _extract_span_field(span: Any, field: str, default: Any = None) -> Any:
    """Safely extract field from ORM model, Pydantic schema, or dictionary."""
    if isinstance(span, dict):
        return span.get(field, default)
    return getattr(span, field, default)


def build_topology_from_spans(
    spans: Sequence[SpanRecordSchema | TraceSpanModel | dict[str, Any]],
    time_window_start: datetime | None = None,
    time_window_end: datetime | None = None,
    version: str = "v1.0.0",
    data_source: str = "trace_spans",
) -> TopologyGraph:
    """Deterministically reconstruct directed service dependency graph from trace parent-child spans.

    Edge convention:
      caller_service -> downstream_service
    """
    now = datetime.now(UTC)
    graph = TopologyGraph(
        version=version,
        data_source=data_source,
        time_window_start=_to_utc(time_window_start) if time_window_start else now,
        time_window_end=_to_utc(time_window_end) if time_window_end else now,
    )

    if not spans:
        graph.diagnostics = {
            "total_spans": 0,
            "traces_evaluated": 0,
            "edges_discovered": 0,
            "malformed_spans": 0,
            "unresolved_parents": 0,
            "intra_service_calls": 0,
        }
        return graph

    # Group spans by trace_id and partition
    spans_by_trace: dict[str, list[Any]] = defaultdict(list)
    malformed_count = 0

    all_timestamps: list[datetime] = []

    for sp in spans:
        trace_id = _extract_span_field(sp, "trace_id")
        span_id = _extract_span_field(sp, "span_id")
        service_name = _extract_span_field(sp, "service_name")
        start_time = _extract_span_field(sp, "start_time")

        if not trace_id or not span_id or not service_name:
            malformed_count += 1
            continue

        parent_id = _extract_span_field(sp, "parent_span_id")
        if parent_id and parent_id == span_id:  # Self-loop span is invalid
            malformed_count += 1
            continue

        if start_time:
            if not isinstance(start_time, datetime):
                start_time = datetime.fromisoformat(str(start_time))
            st_utc = _to_utc(start_time)
            if time_window_start and st_utc < _to_utc(time_window_start):
                continue
            if time_window_end and st_utc > _to_utc(time_window_end):
                continue
            all_timestamps.append(st_utc)

        spans_by_trace[str(trace_id)].append(sp)
        graph.add_node(str(service_name))

    if all_timestamps and time_window_start is None:
        graph.time_window_start = min(all_timestamps)
    if all_timestamps and time_window_end is None:
        graph.time_window_end = max(all_timestamps)

    # Edge observations: (caller, downstream) -> list of (duration_ms, is_error, timestamp)
    edge_observations: dict[tuple[str, str], list[tuple[float, bool, datetime]]] = defaultdict(list)

    unresolved_parents = 0
    intra_service_calls = 0

    # Process each trace deterministically
    for trace_id in sorted(spans_by_trace.keys()):
        trace_spans = spans_by_trace[trace_id]
        span_map: dict[str, Any] = {str(_extract_span_field(s, "span_id")): s for s in trace_spans}

        for child in trace_spans:
            parent_id = _extract_span_field(child, "parent_span_id")
            if not parent_id:
                continue

            parent = span_map.get(str(parent_id))
            if not parent:
                unresolved_parents += 1
                continue

            caller = str(_extract_span_field(parent, "service_name"))
            downstream = str(_extract_span_field(child, "service_name"))

            if caller == downstream:
                intra_service_calls += 1
                continue

            # Directed cross-service call: caller -> downstream
            dur = _extract_span_field(child, "duration_ms", 0.0)
            status_code = str(_extract_span_field(child, "status_code", "OK")).upper()
            is_err = status_code == "ERROR"
            c_time = _extract_span_field(child, "start_time")
            if not isinstance(c_time, datetime):
                c_time = datetime.fromisoformat(str(c_time)) if c_time else now
            c_time = _to_utc(c_time)

            edge_observations[(caller, downstream)].append((float(dur), is_err, c_time))

    # Aggregate observations into DependencyEdge objects
    for (caller, downstream), obs_list in sorted(edge_observations.items()):
        durations = [o[0] for o in obs_list]
        errors = [o[1] for o in obs_list]
        timestamps = [o[2] for o in obs_list]

        call_count = len(obs_list)
        error_count = sum(1 for e in errors if e)
        error_rate = round(error_count / max(1, call_count), 4)

        lat_arr = np.array(durations, dtype=float)
        p50 = round(float(np.percentile(lat_arr, 50)), 2)
        p95 = round(float(np.percentile(lat_arr, 95)), 2)
        mean_lat = round(float(np.mean(lat_arr)), 2)

        edge = DependencyEdge(
            caller_service=caller,
            downstream_service=downstream,
            call_count=call_count,
            error_count=error_count,
            error_rate=error_rate,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            latency_mean_ms=mean_lat,
            first_seen=min(timestamps),
            last_seen=max(timestamps),
            is_observed=True,
            is_manual=False,
            metadata={
                "sample_trace_ids": list(
                    {
                        str(_extract_span_field(s, "trace_id"))
                        for s in spans_by_trace.get(trace_id, [])
                    }
                )[:5]
            },
        )
        graph.add_edge(edge)

    graph.diagnostics = {
        "total_spans": len(spans),
        "traces_evaluated": len(spans_by_trace),
        "edges_discovered": len(graph.edge_list),
        "malformed_spans": malformed_count,
        "unresolved_parents": unresolved_parents,
        "intra_service_calls": intra_service_calls,
    }

    return graph
