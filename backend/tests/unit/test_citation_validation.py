"""Unit tests for evidence citation validation and report grounding."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.evidence import (
    EvidenceBank,
    EvidenceRecord,
    EvidenceSourceType,
    validate_citations,
)
from app.agent.reporting import InvestigationReport


def test_citation_validation_all_valid() -> None:
    """Test citation validation passes when all IDs exist in evidence bank."""
    bank = EvidenceBank()
    rec1 = EvidenceRecord.create(
        source_type=EvidenceSourceType.METRIC,
        service_name="payment-service",
        timestamp=datetime.now(UTC),
        summary="High latency observed",
    )
    rec2 = EvidenceRecord.create(
        source_type=EvidenceSourceType.LOG,
        service_name="payment-service",
        timestamp=datetime.now(UTC),
        summary="Error spike log",
    )
    bank.add(rec1)
    bank.add(rec2)

    result = validate_citations([rec1.id, rec2.id], bank)
    assert result.is_valid is True
    assert result.citation_validity_percentage == 100.0
    assert len(result.invalid_citations) == 0


def test_citation_validation_detects_fabricated_citations() -> None:
    """Test citation validation detects hallucinated or unknown citation IDs."""
    bank = EvidenceBank()
    rec1 = EvidenceRecord.create(
        source_type=EvidenceSourceType.ANOMALY,
        service_name="order-service",
        timestamp=datetime.now(UTC),
        summary="Anomaly detected",
    )
    bank.add(rec1)

    claimed_ids = [rec1.id, "ev-hallucinated-id-9999", "ev-fake-metric-service-1234"]
    result = validate_citations(claimed_ids, bank)
    assert result.is_valid is False
    assert result.missing_citations_count == 2
    assert "ev-hallucinated-id-9999" in result.invalid_citations
    assert len(result.violations) == 2


def test_investigation_report_validate_all_citations() -> None:
    """Test InvestigationReport.validate_all_citations method."""
    bank = EvidenceBank()
    rec = EvidenceRecord.create(
        source_type=EvidenceSourceType.TOPOLOGY,
        service_name="payment-service",
        timestamp=datetime.now(UTC),
        summary="Topology edge",
    )
    bank.add(rec)

    now = datetime.now(UTC)
    report = InvestigationReport(
        investigation_id="inv-test",
        incident_id="inc-test",
        time_window_start=now,
        time_window_end=now,
        summary="Test investigation",
        severity="HIGH",
        confidence_classification="HIGH",
        confidence_score=0.9,
        supporting_evidence_citations=[rec.id],
        recommended_diagnostic_step="Inspect logs",
    )

    valid = report.validate_all_citations(bank)
    assert valid is True
    assert report.citation_validation is not None
    assert report.citation_validation.is_valid is True

    # Now add a fabricated citation to report
    report.supporting_evidence_citations.append("ev-fake-id")
    valid_after = report.validate_all_citations(bank)
    assert valid_after is False
    assert report.citation_validation.is_valid is False
