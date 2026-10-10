"""Evidence collection, immutable evidence record typing, and citation validation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvidenceSourceType(StrEnum):
    """Categorical source type for collected incident evidence."""

    ANOMALY = "anomaly"
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    TOPOLOGY = "topology"
    RCA_CANDIDATE = "rca_candidate"
    RUNBOOK = "runbook"


class EvidenceRecord(BaseModel):
    """Immutable evidence unit gathered during investigation."""

    id: str = Field(
        description="Unique deterministic identifier ev-{source_type}-{service}-{hash[:8]}"
    )
    source_type: EvidenceSourceType = Field(description="Telemetry or analytical origin")
    service_name: str = Field(description="Target service associated with this evidence")
    timestamp: datetime = Field(description="Observation or event timestamp in UTC")
    summary: str = Field(description="Human-readable factual summary of the observation")
    raw_payload: dict[str, Any] = Field(default_factory=dict, description="Bounded raw details")

    @classmethod
    def create(
        cls,
        source_type: EvidenceSourceType,
        service_name: str,
        timestamp: datetime,
        summary: str,
        raw_payload: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        """Construct an evidence record with a deterministic stable ID."""
        ts_utc = timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
        payload = raw_payload or {}

        # Build stable content hash
        hash_content = (
            f"{source_type.value}|{service_name}|{ts_utc.isoformat()}|{summary}|"
            f"{json.dumps(payload, sort_keys=True, default=str)}"
        )
        content_hash = hashlib.sha256(hash_content.encode("utf-8")).hexdigest()[:8]
        safe_service = service_name.replace(" ", "_").replace("/", "_")[:24]
        record_id = f"ev-{source_type.value}-{safe_service}-{content_hash}"

        return cls(
            id=record_id,
            source_type=source_type,
            service_name=service_name,
            timestamp=ts_utc,
            summary=summary,
            raw_payload=payload,
        )


class EvidenceBank(BaseModel):
    """In-memory collection of evidence records collected for an investigation."""

    records: dict[str, EvidenceRecord] = Field(default_factory=dict)

    def add(self, record: EvidenceRecord) -> str:
        """Add an evidence record and return its unique ID."""
        self.records[record.id] = record
        return record.id

    def get(self, record_id: str) -> EvidenceRecord | None:
        """Retrieve an evidence record by its ID."""
        return self.records.get(record_id)

    def list_by_service(self, service_name: str) -> list[EvidenceRecord]:
        """List all evidence records associated with a specific service."""
        return [r for r in self.records.values() if r.service_name == service_name]

    def list_by_source(self, source_type: EvidenceSourceType) -> list[EvidenceRecord]:
        """List all evidence records from a specific source type."""
        return [r for r in self.records.values() if r.source_type == source_type]

    def all_ids(self) -> set[str]:
        """Return the set of all gathered evidence record IDs."""
        return set(self.records.keys())


class CitationValidationResult(BaseModel):
    """Result of citation verification against an investigation's evidence bank."""

    is_valid: bool
    total_citations: int
    valid_citations: list[str]
    invalid_citations: list[str]
    missing_citations_count: int
    citation_validity_percentage: float
    violations: list[str] = Field(default_factory=list)


def validate_citations(
    claimed_citation_ids: list[str],
    evidence_bank: EvidenceBank,
) -> CitationValidationResult:
    """Validate that all claimed citation IDs resolve to existing evidence records in the bank."""
    if not claimed_citation_ids:
        return CitationValidationResult(
            is_valid=True,
            total_citations=0,
            valid_citations=[],
            invalid_citations=[],
            missing_citations_count=0,
            citation_validity_percentage=100.0,
            violations=[],
        )

    valid: list[str] = []
    invalid: list[str] = []
    violations: list[str] = []

    for cit_id in claimed_citation_ids:
        if evidence_bank.get(cit_id) is not None:
            valid.append(cit_id)
        else:
            invalid.append(cit_id)
            violations.append(
                f"Fabricated or unresolvable citation ID '{cit_id}' not found in evidence bank."
            )

    total = len(claimed_citation_ids)
    valid_count = len(valid)
    validity_pct = round((valid_count / total) * 100.0, 2) if total > 0 else 100.0
    is_valid = len(invalid) == 0

    return CitationValidationResult(
        is_valid=is_valid,
        total_citations=total,
        valid_citations=valid,
        invalid_citations=invalid,
        missing_citations_count=len(invalid),
        citation_validity_percentage=validity_pct,
        violations=violations,
    )
