"""Bounded tool for retrieving Phase 4 ranked root-cause candidates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.evidence import EvidenceRecord, EvidenceSourceType
from app.agent.tools.base import BaseInvestigationTool
from app.db.models.topology import IncidentAnalysisModel
from app.rca.schemas import RootCauseAnalysisRequest
from app.rca.service import RootCauseAnalysisService
from app.topology.models import _to_utc


class RootCauseCandidatesInput(BaseModel):
    """Input parameters for querying Phase 4 root-cause candidates."""

    incident_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = 5


class GetRootCauseCandidatesTool(BaseInvestigationTool):
    """Tool retrieving Phase 4 explainable root-cause ranking candidates."""

    name = "get_root_cause_candidates"
    description = "Retrieve Phase 4 ranked root-cause candidates, scores, and path explanations."

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        db: Session = kwargs["db"]
        inp = RootCauseCandidatesInput(
            incident_id=kwargs.get("incident_id"),
            start_time=kwargs.get("start_time"),
            end_time=kwargs.get("end_time"),
            limit=kwargs.get("limit", 5),
        )

        candidates_data: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []

        # Check if an existing analysis record exists for this incident ID
        record: IncidentAnalysisModel | None = None
        if inp.incident_id:
            stmt = (
                select(IncidentAnalysisModel)
                .where(IncidentAnalysisModel.incident_id == inp.incident_id)
                .order_by(IncidentAnalysisModel.created_at.desc())
                .limit(1)
            )
            record = db.execute(stmt).scalars().first()

        now_utc = datetime.now(UTC)

        if record:
            candidates_data = record.candidates_json[: inp.limit]
            for cand in candidates_data:
                svc = cand.get("service_name", "unknown")
                score = cand.get("composite_score", cand.get("score", 0.0))
                rank = cand.get("rank", 1)
                ev = EvidenceRecord.create(
                    source_type=EvidenceSourceType.RCA_CANDIDATE,
                    service_name=svc,
                    timestamp=now_utc,
                    summary=f"Phase 4 Rank #{rank} Root-Cause Candidate '{svc}' (score={score:.3f}): {cand.get('explanation', '')}",
                    raw_payload=cand,
                )
                evidence_items.append(ev.model_dump())

            return {
                "incident_id": inp.incident_id,
                "analysis_id": record.id,
                "top_candidate": record.top_candidate_service,
                "candidates": candidates_data,
                "evidence": evidence_items,
            }, len(candidates_data)

        # If no existing record and time window is given, run Phase 4 RCA service
        if inp.start_time and inp.end_time:
            rca_service = RootCauseAnalysisService()
            req = RootCauseAnalysisRequest(
                incident_id=inp.incident_id or f"inc-{uuid.uuid4().hex[:8]}",
                time_window_start=_to_utc(inp.start_time),
                time_window_end=_to_utc(inp.end_time),
            )
            resp = rca_service.analyze(req, db)
            for cand_obj in resp.candidates[: inp.limit]:
                c_dict = cand_obj.model_dump()
                candidates_data.append(c_dict)
                ev = EvidenceRecord.create(
                    source_type=EvidenceSourceType.RCA_CANDIDATE,
                    service_name=cand_obj.service_name,
                    timestamp=now_utc,
                    summary=f"Phase 4 Rank #{cand_obj.rank} Candidate '{cand_obj.service_name}' (score={cand_obj.score:.3f}): {cand_obj.explanation}",
                    raw_payload=c_dict,
                )
                evidence_items.append(ev.model_dump())

            return {
                "incident_id": resp.incident_id,
                "analysis_id": resp.analysis_id,
                "top_candidate": resp.top_candidate_service,
                "candidates": candidates_data,
                "evidence": evidence_items,
            }, len(candidates_data)

        return {
            "message": "No root cause candidates found for the specified incident or window.",
            "candidates": [],
            "evidence": [],
        }, 0
