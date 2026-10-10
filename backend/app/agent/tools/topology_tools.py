"""Bounded tool for querying service topology and dependency paths."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.evidence import EvidenceRecord, EvidenceSourceType
from app.agent.tools.base import BaseInvestigationTool
from app.topology.service import TopologyService


class TopologyQueryInput(BaseModel):
    """Input parameters for querying service topology."""

    service_name: str | None = None
    topology_id: str | None = None


class GetServiceTopologyTool(BaseInvestigationTool):
    """Tool querying service topology graph, dependency edges, and upstream/downstream callers."""

    name = "get_service_topology"
    description = "Retrieve the service dependency topology graph and upstream/downstream callers."

    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], int]:
        db: Session = kwargs["db"]
        inp = TopologyQueryInput(
            service_name=kwargs.get("service_name"),
            topology_id=kwargs.get("topology_id"),
        )

        topo_service = TopologyService()
        if inp.topology_id:
            snapshot = topo_service.get_topology_by_id_or_version(db, inp.topology_id)
        else:
            snapshot = topo_service.get_latest_topology(db)

        if not snapshot:
            return {
                "message": "No topology snapshot available in database",
                "edges": [],
                "evidence": [],
            }, 0

        edges = topo_service.get_edges_for_snapshot(db, snapshot.id)
        edge_data: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []

        now_utc = datetime.now(UTC)

        for e in edges:
            match = True
            if inp.service_name:
                match = (e.caller_service == inp.service_name) or (
                    e.downstream_service == inp.service_name
                )

            if match:
                edge_entry = {
                    "caller_service": e.caller_service,
                    "downstream_service": e.downstream_service,
                    "call_count": e.call_count,
                    "error_count": e.error_count,
                    "error_rate": round(e.error_rate, 4),
                    "latency_p95_ms": e.latency_p95_ms,
                }
                edge_data.append(edge_entry)

                ev = EvidenceRecord.create(
                    source_type=EvidenceSourceType.TOPOLOGY,
                    service_name=e.downstream_service,
                    timestamp=now_utc,
                    summary=f"Dependency edge: {e.caller_service} calls {e.downstream_service} (calls={e.call_count}, err_rate={e.error_rate:.2%}, p95={e.latency_p95_ms or 0:.1f}ms)",
                    raw_payload=edge_entry,
                )
                evidence_items.append(ev.model_dump())

        return {
            "snapshot_id": snapshot.id,
            "version": snapshot.version,
            "services": snapshot.services_json or [],
            "edges": edge_data,
            "evidence": evidence_items,
        }, len(edge_data)
