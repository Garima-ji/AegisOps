"""Typed Pydantic schemas for anomaly correlation, root-cause candidate evidence, and incident ranking."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnomalyEvent(BaseModel):
    """Normalized operational anomaly event used for incident correlation."""

    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(
        min_length=1, max_length=64, description="Service emitting or subject to anomaly"
    )
    timestamp: datetime = Field(description="Anomaly onset or detection timestamp")
    detector_name: str = Field(default="unknown", description="Detector engine or statistical rule")
    anomaly_score: float = Field(ge=0.0, le=1.0, description="Normalized anomaly confidence score")
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(default="MEDIUM")
    triggering_features: list[str] = Field(
        default_factory=list, description="Contributed metric/log features"
    )
    explanation: str | None = Field(default=None, description="Detector explanation")
    trace_id: str | None = Field(
        default=None, description="Associated trace ID if tied to specific request"
    )


class CorrelatedIncident(BaseModel):
    """Cluster of temporally and topologically correlated anomalies across services."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(description="Deterministic or UUID incident cluster identifier")
    start_time: datetime = Field(description="Earliest anomaly onset timestamp in cluster")
    end_time: datetime = Field(description="Latest anomaly timestamp in cluster")
    duration_seconds: float = Field(
        ge=0.0, description="Duration span of correlated incident window"
    )
    affected_services: list[str] = Field(
        description="List of distinct services experiencing anomalies"
    )
    events: list[AnomalyEvent] = Field(
        default_factory=list, description="Raw anomaly events within cluster"
    )
    trace_ids: list[str] = Field(
        default_factory=list, description="Associated trace IDs observed across events"
    )
    topology_version: str | None = Field(
        default=None, description="Version of topology snapshot used for correlation"
    )


class CandidateEvidence(BaseModel):
    """Machine-readable evidence supporting or contradicting a root-cause candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_service: str = Field(description="Candidate service name")
    anomaly_onset: datetime | None = Field(
        default=None, description="Earliest anomaly onset for this service"
    )
    relative_delay_sec: float | None = Field(
        default=None, description="Seconds elapsed after the earliest incident anomaly"
    )
    is_innermost_callee: bool = Field(
        default=False, description="True if spans indicate service was the leaf failing callee"
    )
    failing_span_count: int = Field(
        default=0, ge=0, description="Observed error spans within the incident window"
    )
    downstream_symptoms: list[str] = Field(
        default_factory=list, description="Affected services downstream reachable from candidate"
    )
    upstream_callers: list[str] = Field(
        default_factory=list, description="Affected services upstream that call candidate"
    )
    anomaly_severity: str = Field(
        default="UNKNOWN", description="Highest severity observed for candidate"
    )
    mean_anomaly_score: float = Field(
        ge=0.0, le=1.0, default=0.0, description="Mean anomaly confidence score"
    )
    detector_count: int = Field(
        default=0, ge=0, description="Number of independent detectors flagging this service"
    )
    positive_indicators: list[str] = Field(
        default_factory=list, description="Evidence factors positively supporting candidate"
    )
    contradictory_indicators: list[str] = Field(
        default_factory=list, description="Evidence factors questioning or weakening candidate"
    )
    uncertainty_reasons: list[str] = Field(
        default_factory=list, description="Telemetry gaps, missing edges, or ambiguity statements"
    )


class RankedCandidate(BaseModel):
    """Ranked root-cause candidate with deterministic scores and transparent explanations."""

    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(description="Evaluated service name")
    rank: int = Field(ge=1, description="1-based rank position")
    score: float = Field(
        ge=0.0, le=1.0, description="Normalized deterministic root-cause heuristic score"
    )
    score_breakdown: dict[str, float] = Field(
        description="Transparent sub-scores: origin_score, temporal_score, impact_score, severity_score"
    )
    evidence: CandidateEvidence = Field(description="Machine-readable evidence object")
    explanation: str = Field(description="Deterministic, non-hallucinated evidence summary")


class RootCauseAnalysisRequest(BaseModel):
    """API request payload to execute root-cause analysis over an incident window."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str | None = Field(
        default=None, description="Optional existing incident identifier"
    )
    time_window_start: datetime | None = Field(
        default=None, description="Start timestamp of incident investigation window"
    )
    time_window_end: datetime | None = Field(
        default=None, description="End timestamp (defaults to current UTC if omitted)"
    )
    lookback_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Lookback window in minutes if time_window_start is omitted",
    )
    correlation_window_seconds: float = Field(
        default=180.0, ge=10.0, le=3600.0, description="Maximum temporal gap to cluster anomalies"
    )
    topology_id: str | None = Field(
        default=None, description="Specific topology snapshot ID; uses latest snapshot if omitted"
    )
    persist: bool = Field(default=True, description="Whether to persist analysis run to database")


class RootCauseAnalysisResponse(BaseModel):
    """Structured response detailing ranked root-cause candidates and topology context."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(description="Unique UUID analysis identifier")
    incident_id: str | None = Field(default=None, description="Correlated incident identifier")
    status: Literal["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED"] = Field(
        description="Analysis resolution status"
    )
    time_window_start: datetime = Field(description="Effective window start timestamp (UTC)")
    time_window_end: datetime = Field(description="Effective window end timestamp (UTC)")
    topology_version: str | None = Field(
        default=None, description="Topology snapshot identifier used"
    )
    affected_services: list[str] = Field(
        default_factory=list, description="Services detected with anomalies"
    )
    top_candidate_service: str | None = Field(
        default=None, description="Highest-ranked candidate service (None if insufficient evidence)"
    )
    confidence_score: float | None = Field(
        default=None, description="Deterministic confidence score for top candidate (0.0 - 1.0)"
    )
    summary_explanation: str = Field(description="Deterministic summary explanation of findings")
    candidates: list[RankedCandidate] = Field(
        default_factory=list, description="Deterministically ranked candidates list"
    )
    diagnostics: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata on events evaluated, trace count, graph size, execution time",
    )
    created_at: datetime = Field(description="Timestamp analysis was computed")


class IncidentAnalysisSummary(BaseModel):
    """Paginated list summary item for previous incident analyses."""

    model_config = ConfigDict(extra="forbid")

    id: str
    incident_id: str | None
    status: str
    time_window_start: datetime
    time_window_end: datetime
    topology_version: str | None
    affected_services: list[str]
    top_candidate_service: str | None
    confidence_score: float | None
    created_at: datetime


class IncidentAnalysisListResponse(BaseModel):
    """Paginated response for root-cause analyses list."""

    model_config = ConfigDict(extra="forbid")

    total: int
    items: list[IncidentAnalysisSummary]
    limit: int
    offset: int
