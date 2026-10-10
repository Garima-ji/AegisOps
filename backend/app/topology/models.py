"""Domain models and directed graph representations for service dependency topology."""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _to_utc(dt: datetime) -> datetime:
    """Ensure timestamp is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class ServiceNode:
    """Represents a service node in the dependency topology."""

    name: str
    environment: str = "production"
    status: str = "healthy"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DependencyEdge:
    """Directed dependency edge representing caller -> downstream invocation."""

    caller_service: str
    downstream_service: str
    call_count: int = 1
    error_count: int = 0
    error_rate: float = 0.0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_mean_ms: float | None = None
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_observed: bool = True
    is_manual: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "caller_service": self.caller_service,
            "downstream_service": self.downstream_service,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_mean_ms": self.latency_mean_ms,
            "first_seen": _to_utc(self.first_seen).isoformat(),
            "last_seen": _to_utc(self.last_seen).isoformat(),
            "is_observed": self.is_observed,
            "is_manual": self.is_manual,
            "metadata": self.metadata,
        }


@dataclass
class TopologyGraph:
    """Directed graph representing service dependencies with cycle detection and bounded traversal."""

    version: str = "v1.0.0"
    data_source: str = "trace_spans"
    time_window_start: datetime = field(default_factory=lambda: datetime.now(UTC))
    time_window_end: datetime = field(default_factory=lambda: datetime.now(UTC))
    nodes: dict[str, ServiceNode] = field(default_factory=dict)
    # Directed adjacency: caller -> dict[downstream, DependencyEdge]
    _downstream_adj: dict[str, dict[str, DependencyEdge]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    # Reverse adjacency: downstream -> dict[caller, DependencyEdge]
    _upstream_adj: dict[str, dict[str, DependencyEdge]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def add_node(
        self,
        name: str,
        environment: str = "production",
        status: str = "healthy",
        metadata: dict[str, Any] | None = None,
    ) -> ServiceNode:
        """Add or update a service node."""
        if name not in self.nodes:
            self.nodes[name] = ServiceNode(
                name=name,
                environment=environment,
                status=status,
                metadata=metadata or {},
            )
        return self.nodes[name]

    def add_service(
        self,
        name: str,
        environment: str = "production",
        status: str = "healthy",
        metadata: dict[str, Any] | None = None,
    ) -> ServiceNode:
        """Alias for add_node."""
        return self.add_node(name=name, environment=environment, status=status, metadata=metadata)

    def add_edge(
        self,
        edge_or_caller: DependencyEdge | str | None = None,
        downstream: str | None = None,
        caller: str | None = None,
        call_count: int = 1,
        error_count: int = 0,
        error_rate: float = 0.0,
        latency_p50_ms: float | None = None,
        latency_p95_ms: float | None = None,
        is_observed: bool = True,
        is_manual: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> DependencyEdge:
        """Add or update a directed dependency edge (caller -> downstream)."""
        if isinstance(edge_or_caller, DependencyEdge):
            edge = edge_or_caller
        else:
            caller_name = caller or (edge_or_caller if isinstance(edge_or_caller, str) else None)
            if not caller_name or not downstream:
                raise ValueError("caller and downstream must be specified")
            edge = DependencyEdge(
                caller_service=caller_name,
                downstream_service=downstream,
                call_count=call_count,
                error_count=error_count,
                error_rate=error_rate,
                latency_p50_ms=latency_p50_ms,
                latency_p95_ms=latency_p95_ms,
                is_observed=is_observed,
                is_manual=is_manual,
                metadata=metadata or {},
            )

        self.add_node(edge.caller_service)
        self.add_node(edge.downstream_service)

        c_name = edge.caller_service
        d_name = edge.downstream_service

        self._downstream_adj[c_name][d_name] = edge
        self._upstream_adj[d_name][c_name] = edge
        return edge

    def get_edge(self, caller: str, downstream: str) -> DependencyEdge | None:
        """Return the directed edge between caller and downstream if present."""
        return self._downstream_adj.get(caller, {}).get(downstream)

    def get_direct_downstream(self, service_name: str) -> list[str]:
        """Return direct dependencies called by this service (caller -> downstream)."""
        return sorted(self._downstream_adj.get(service_name, {}).keys())

    def get_direct_callers(self, service_name: str) -> list[str]:
        """Return direct upstream callers that invoke this service (caller -> this service)."""
        return sorted(self._upstream_adj.get(service_name, {}).keys())

    def get_all_downstream(self, service_name: str, max_depth: int = 5) -> set[str]:
        """Traverse downstream dependencies reachable from service_name with cycle prevention."""
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(service_name, 0)])

        while queue:
            curr, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for nxt in self.get_direct_downstream(curr):
                if nxt not in visited and nxt != service_name:
                    visited.add(nxt)
                    queue.append((nxt, depth + 1))

        return visited

    def get_all_upstream(self, service_name: str, max_depth: int = 5) -> set[str]:
        """Traverse upstream callers that reach service_name with cycle prevention."""
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(service_name, 0)])

        while queue:
            curr, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for caller in self.get_direct_callers(curr):
                if caller not in visited and caller != service_name:
                    visited.add(caller)
                    queue.append((caller, depth + 1))

        return visited

    def find_paths(
        self,
        source: str,
        target: str,
        max_depth: int = 5,
    ) -> list[list[str]]:
        """Find all directed simple paths from source to target within depth bound."""
        if source == target or source not in self.nodes or target not in self.nodes:
            return []

        paths: list[list[str]] = []

        def _dfs(current: str, current_path: list[str], depth: int) -> None:
            if current == target:
                paths.append(list(current_path))
                return
            if depth >= max_depth:
                return

            for nxt in self.get_direct_downstream(current):
                if nxt not in current_path:  # Cycle prevention
                    current_path.append(nxt)
                    _dfs(nxt, current_path, depth + 1)
                    current_path.pop()

        _dfs(source, [source], 0)
        return sorted(paths, key=len)

    def find_path(self, source: str, target: str, max_depth: int = 5) -> list[str] | None:
        """Find the shortest directed path from source to target."""
        paths = self.find_paths(source, target, max_depth=max_depth)
        return paths[0] if paths else None

    def has_cycle(self) -> bool:
        """Detect if the directed dependency topology contains circular dependencies."""
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def _is_cyclic(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for neighbor in self.get_direct_downstream(node):
                if neighbor not in visited:
                    if _is_cyclic(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        for node in sorted(self.nodes.keys()):
            if node not in visited:
                if _is_cyclic(node):
                    return True
        return False

    @property
    def service_names(self) -> list[str]:
        return sorted(self.nodes.keys())

    @property
    def edge_list(self) -> list[DependencyEdge]:
        edges: list[DependencyEdge] = []
        for caller in sorted(self._downstream_adj.keys()):
            for downstream in sorted(self._downstream_adj[caller].keys()):
                edges.append(self._downstream_adj[caller][downstream])
        return edges

    @property
    def edges(self) -> dict[tuple[str, str], DependencyEdge]:
        """Dictionary of directed edges keyed by (caller_service, downstream_service)."""
        edge_map: dict[tuple[str, str], DependencyEdge] = {}
        for caller, downstreams in self._downstream_adj.items():
            for downstream, edge in downstreams.items():
                edge_map[(caller, downstream)] = edge
        return edge_map

    def to_dict(self) -> dict[str, Any]:
        """Serialize topology graph to a versioned dictionary."""
        return {
            "version": self.version,
            "data_source": self.data_source,
            "time_window_start": _to_utc(self.time_window_start).isoformat(),
            "time_window_end": _to_utc(self.time_window_end).isoformat(),
            "service_count": len(self.nodes),
            "edge_count": len(self.edge_list),
            "services": self.service_names,
            "edges": [e.to_dict() for e in self.edge_list],
            "has_cycle": self.has_cycle(),
            "diagnostics": self.diagnostics,
        }
