"""Bounded tool for retrieving versioned local operational runbooks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agent.evidence import EvidenceRecord, EvidenceSourceType
from app.agent.runbooks.catalog import RunbookRetriever
from app.agent.tools.base import BaseInvestigationTool


class RunbookQueryInput(BaseModel):
    """Input parameters for retrieving runbooks."""

    query: str = Field(min_length=2, max_length=200)
    target_service: str | None = None
    limit: int = Field(default=3, ge=1, le=5)


class RetrieveRunbookTool(BaseInvestigationTool):
    """Tool retrieving relevant runbook sections using local keyword/token matching."""

    name = "retrieve_runbook"
    description = (
        "Retrieve relevant runbook guidance and remediation steps from local knowledge base."
    )

    def __init__(self, retriever: RunbookRetriever | None = None) -> None:
        self.retriever = retriever or RunbookRetriever()

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        inp = RunbookQueryInput(
            query=kwargs["query"],
            target_service=kwargs.get("target_service"),
            limit=kwargs.get("limit", 3),
        )

        matches = self.retriever.retrieve(
            query=inp.query,
            target_service=inp.target_service,
            limit=inp.limit,
        )

        results: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []
        now_utc = datetime.now(UTC)

        for rb, score in matches:
            rb_dict = {
                "runbook_id": rb.id,
                "title": rb.title,
                "target_service": rb.target_service,
                "symptoms": rb.symptoms,
                "diagnostic_steps": rb.diagnostic_steps,
                "remediation_actions": rb.remediation_actions,
                "score": round(score, 2),
                "excerpt": rb.content[:300],
            }
            results.append(rb_dict)

            ev = EvidenceRecord.create(
                source_type=EvidenceSourceType.RUNBOOK,
                service_name=rb.target_service,
                timestamp=now_utc,
                summary=f"Runbook '{rb.title}' ({rb.id}): {rb.symptoms[0] if rb.symptoms else 'Standard procedure'}. Recommended actions: {', '.join(rb.remediation_actions)}",
                raw_payload={"runbook_id": rb.id, "title": rb.title, "score": score},
            )
            evidence_items.append(ev.model_dump())

        return {
            "query": inp.query,
            "target_service": inp.target_service,
            "runbooks": results,
            "evidence": evidence_items,
        }, len(results)
