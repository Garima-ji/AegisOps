"""Schemas for controlled fault injection scenarios and ground-truth experiment labels."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperimentCreateRequest(BaseModel):
    """Payload to launch a controlled, bounded fault injection scenario."""

    model_config = ConfigDict(extra="forbid")

    scenario_name: str = Field(
        min_length=3,
        max_length=64,
        description="Identifiable scenario key (latency_spike, error_surge, service_timeout)",
    )
    description: str = Field(
        default="", max_length=256, description="Human explanation of test purpose"
    )
    target_service: str = Field(
        min_length=2, max_length=64, description="Service subject to injected fault"
    )
    fault_type: Literal["latency_spike", "error_surge", "service_timeout"] = Field(
        description="Allowlisted fault type"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Bounded scenario parameters (e.g. latency_ms: 500)"
    )
    duration_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Bounded active duration in seconds (5s to 300s)",
    )
    initiating_service: str = Field(
        description="Ground-truth patient-zero service where fault originates"
    )
    symptom_services: list[str] = Field(
        default_factory=list, description="Downstream services expected to show cascading symptoms"
    )


class ExperimentResponse(BaseModel):
    """Experiment lifecycle status representation."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique experiment ID")
    scenario_name: str
    description: str
    target_service: str
    fault_type: str
    parameters: dict[str, Any]
    start_time: datetime
    end_time: datetime | None
    status: Literal["PENDING", "ACTIVE", "COMPLETED", "FAILED", "STOPPED"]
    initiating_service: str
    symptom_services: list[str]
    cleanup_result: str | None = None
    created_at: datetime


class FaultLabelSchema(BaseModel):
    """Ground-truth label for a specific timestamp and service during an experiment."""

    model_config = ConfigDict(extra="forbid")

    id: int
    experiment_id: str
    timestamp: datetime
    service_name: str
    is_fault: bool
    fault_type: str
    severity: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class FaultLabelListResponse(BaseModel):
    """Paginated or complete ground-truth labels for an experiment."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    labels: list[FaultLabelSchema]
    total: int
