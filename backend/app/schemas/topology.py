"""Typed Pydantic schemas for service dependency topology API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TopologyRebuildRequest(BaseModel):
    """Payload to request explicit reconstruction of the service dependency topology."""

    model_config = ConfigDict(extra="forbid")

    start_time: datetime | None = Field(
        default=None, description="Start timestamp of trace observation window"
    )
    end_time: datetime | None = Field(
        default=None,
        description="End timestamp of trace observation window (defaults to current UTC)",
    )
    lookback_minutes: int = Field(
        default=60, ge=1, le=1440, description="Lookback minutes if start_time is omitted"
    )
    version: str | None = Field(
        default=None, max_length=64, description="Optional custom version string for the snapshot"
    )
    data_source: str = Field(
        default="trace_spans",
        max_length=64,
        description="Telemetry data source used for graph extraction",
    )


class TopologyEdgeSchema(BaseModel):
    """Observed directed caller -> downstream dependency edge."""

    model_config = ConfigDict(extra="forbid")

    caller_service: str = Field(description="Initiating/caller service name")
    downstream_service: str = Field(description="Callee/downstream service name")
    call_count: int = Field(ge=0, description="Observed cross-service RPC/HTTP call count")
    error_count: int = Field(ge=0, description="Number of observed failed child spans")
    error_rate: float = Field(ge=0.0, le=1.0, description="Observed error rate")
    latency_p50_ms: float | None = Field(default=None, description="Median latency in milliseconds")
    latency_p95_ms: float | None = Field(
        default=None, description="95th percentile latency in milliseconds"
    )
    first_seen: datetime = Field(description="Earliest span observation timestamp (UTC)")
    last_seen: datetime = Field(description="Most recent span observation timestamp (UTC)")
    is_observed: bool = Field(
        default=True, description="Whether edge was dynamically extracted from traces"
    )
    is_manual: bool = Field(default=False, description="Whether edge was manually configured")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional edge metadata")


class TopologySnapshotResponse(BaseModel):
    """Complete representation of a versioned topology snapshot and its dependency edges."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(description="Unique snapshot identifier")
    version: str = Field(description="Semantic version string of the topology")
    data_source: str = Field(description="Data source used to build snapshot")
    time_window_start: datetime = Field(description="Observation window start (UTC)")
    time_window_end: datetime = Field(description="Observation window end (UTC)")
    service_count: int = Field(ge=0, description="Total unique services present in topology")
    edge_count: int = Field(ge=0, description="Total directed dependency edges")
    services: list[str] = Field(description="List of service node identifiers")
    edges: list[TopologyEdgeSchema] = Field(description="Directed dependency edges")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Snapshot metadata (e.g. cycle checks)"
    )
    created_at: datetime = Field(description="Snapshot generation timestamp (UTC)")


class TopologySnapshotSummary(BaseModel):
    """Summary item for paginated list of topology snapshots."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    version: str
    data_source: str
    time_window_start: datetime
    time_window_end: datetime
    service_count: int
    edge_count: int
    created_at: datetime


class TopologySnapshotListResponse(BaseModel):
    """Paginated list of available topology snapshots."""

    model_config = ConfigDict(extra="forbid")

    total: int
    items: list[TopologySnapshotSummary]
    limit: int
    offset: int


class DependencyLink(BaseModel):
    """Direct dependency link item for a specific service."""

    model_config = ConfigDict(extra="forbid")

    service_name: str
    call_count: int
    error_rate: float
    latency_p95_ms: float | None


class ServiceDependenciesResponse(BaseModel):
    """Upstream callers and downstream dependencies for a given service."""

    model_config = ConfigDict(extra="forbid")

    service_name: str
    topology_id: str
    topology_version: str
    direct_callers: list[dict[str, Any]]
    direct_downstream: list[dict[str, Any]]
    total_callers: int
    total_downstream: int
