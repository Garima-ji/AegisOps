"""Schemas for service registry management."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ServiceSchema(BaseModel):
    """Registered microservice representation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=64, description="Unique service identifier")
    environment: str = Field(default="production", max_length=32)
    version: str | None = Field(default=None, max_length=64)
    status: Literal["healthy", "degraded", "unknown"] = Field(default="healthy")
    first_seen: datetime = Field(description="First recorded signal timestamp")
    last_seen: datetime = Field(description="Most recent heartbeat/signal timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceListResponse(BaseModel):
    """List response of registered microservices."""

    model_config = ConfigDict(extra="forbid")

    items: list[ServiceSchema]
    total: int
