"""Temporal and topological anomaly event correlation engine for incident clustering."""

import hashlib
from collections.abc import Sequence

from app.rca.schemas import AnomalyEvent, CorrelatedIncident
from app.topology.models import TopologyGraph


class AnomalyCorrelator:
    """Correlates individual service anomaly events across time and topology."""

    def __init__(self, default_window_seconds: float = 180.0) -> None:
        self.default_window_seconds = default_window_seconds

    def correlate(
        self,
        events: Sequence[AnomalyEvent],
        topology: TopologyGraph | None = None,
        window_seconds: float | None = None,
        topology_version: str | None = None,
    ) -> list[CorrelatedIncident]:
        """Group anomaly events into distinct correlated incident clusters.

        Correlation criteria:
        1. Temporal proximity: consecutive events within `window_seconds`.
        2. Trace linkage: shared trace_id binds events regardless of temporal gap.
        3. Topological connectivity: services connected via caller -> downstream edges
           remain in the same cluster. Unconnected components with distinct trace sets
           are separated to avoid falsely merging unrelated incidents.
        """
        if not events:
            return []

        effective_window = (
            window_seconds if window_seconds is not None else self.default_window_seconds
        )

        # Clean and sort chronologically
        valid_events = [e for e in events if e.timestamp is not None]
        if not valid_events:
            return []

        sorted_events = sorted(valid_events, key=lambda e: e.timestamp)

        # Stage 1: Group by temporal sliding window
        temporal_clusters: list[list[AnomalyEvent]] = []
        current_cluster: list[AnomalyEvent] = [sorted_events[0]]

        for next_event in sorted_events[1:]:
            last_timestamp = current_cluster[-1].timestamp
            time_diff = (next_event.timestamp - last_timestamp).total_seconds()

            # If within window, or shares trace_id with any event in current cluster
            shares_trace = bool(
                next_event.trace_id
                and any(e.trace_id == next_event.trace_id for e in current_cluster if e.trace_id)
            )

            if time_diff <= effective_window or shares_trace:
                current_cluster.append(next_event)
            else:
                temporal_clusters.append(current_cluster)
                current_cluster = [next_event]

        if current_cluster:
            temporal_clusters.append(current_cluster)

        # Stage 2: Topologically separate disjoint components if topology is provided
        final_clusters: list[list[AnomalyEvent]] = []
        for cluster in temporal_clusters:
            sub_clusters = self._split_by_topology_and_traces(cluster, topology)
            final_clusters.extend(sub_clusters)

        # Stage 3: Build typed CorrelatedIncident objects
        incidents: list[CorrelatedIncident] = []
        for cluster_events in final_clusters:
            if not cluster_events:
                continue

            cluster_sorted = sorted(cluster_events, key=lambda e: e.timestamp)
            start_time = cluster_sorted[0].timestamp
            end_time = cluster_sorted[-1].timestamp
            duration = max(0.0, (end_time - start_time).total_seconds())

            # Distinct affected services preserving onset order
            affected_services: list[str] = []
            for ev in cluster_sorted:
                if ev.service_name not in affected_services:
                    affected_services.append(ev.service_name)

            # Unique trace IDs
            trace_ids = sorted({ev.trace_id for ev in cluster_events if ev.trace_id})

            # Deterministic incident ID derived from services and start timestamp
            hash_input = f"{start_time.isoformat()}:{','.join(sorted(affected_services))}"
            incident_id = f"inc-{hashlib.sha256(hash_input.encode('utf-8')).hexdigest()[:12]}"

            incidents.append(
                CorrelatedIncident(
                    incident_id=incident_id,
                    start_time=start_time,
                    end_time=end_time,
                    duration_seconds=duration,
                    affected_services=affected_services,
                    events=cluster_sorted,
                    trace_ids=trace_ids,
                    topology_version=topology_version,
                )
            )

        return incidents

    def _split_by_topology_and_traces(
        self,
        cluster: list[AnomalyEvent],
        topology: TopologyGraph | None,
    ) -> list[list[AnomalyEvent]]:
        """Separate disjoint sub-graphs if multiple unrelated services happen in same time window."""
        services = {e.service_name for e in cluster}
        if len(services) <= 1 or topology is None or len(topology.nodes) == 0:
            return [cluster]

        # Build union-find / adjacency for services in this cluster
        parent: dict[str, str] = {s: s for s in services}

        def find(u: str) -> str:
            if parent[u] != u:
                parent[u] = find(parent[u])
            return parent[u]

        def union(u: str, v: str) -> None:
            root_u = find(u)
            root_v = find(v)
            if root_u != root_v:
                parent[root_u] = root_v

        # 1. Union if they share a trace_id
        trace_to_services: dict[str, list[str]] = {}
        for ev in cluster:
            if ev.trace_id:
                trace_to_services.setdefault(ev.trace_id, []).append(ev.service_name)

        for s_list in trace_to_services.values():
            first = s_list[0]
            for other in s_list[1:]:
                union(first, other)

        # 2. Union if there is a topological path between them (in either direction)
        svc_list = list(services)
        for i, s1 in enumerate(svc_list):
            for s2 in svc_list[i + 1 :]:
                # Check direct or transitive connection in topology
                downstream_s1 = topology.get_all_downstream(s1)
                downstream_s2 = topology.get_all_downstream(s2)
                if s2 in downstream_s1 or s1 in downstream_s2:
                    union(s1, s2)

        # Group events by their service's root cluster
        groups: dict[str, list[AnomalyEvent]] = {}
        for ev in cluster:
            root = find(ev.service_name)
            groups.setdefault(root, []).append(ev)

        return list(groups.values())
