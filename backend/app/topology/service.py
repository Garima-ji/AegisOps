"""Service layer for topology snapshot persistence, retrieval, and dependency queries."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.telemetry import TraceSpanModel
from app.db.models.topology import TopologyEdgeModel, TopologySnapshotModel
from app.topology.extractor import build_topology_from_spans
from app.topology.models import _to_utc


class TopologyService:
    """Service managing database persistence, snapshotting, and querying of service topology."""

    @staticmethod
    def rebuild_topology(
        session: Session,
        start_time: datetime,
        end_time: datetime,
        version: str | None = None,
        data_source: str = "trace_spans",
        limit: int = 50000,
    ) -> TopologySnapshotModel:
        """Query bounded trace spans, reconstruct topology graph, and persist snapshot."""
        st = _to_utc(start_time)
        et = _to_utc(end_time)

        if st >= et:
            raise ValueError(
                f"start_time ({st.isoformat()}) must precede end_time ({et.isoformat()})"
            )

        # Query bounded trace spans
        stmt = (
            select(TraceSpanModel)
            .where(
                TraceSpanModel.start_time >= st,
                TraceSpanModel.start_time <= et,
            )
            .order_by(TraceSpanModel.start_time.asc())
            .limit(limit)
        )
        spans = list(session.execute(stmt).scalars().all())

        if not spans:
            raise ValueError(
                f"No trace spans observed in time window [{st.isoformat()} -> {et.isoformat()}]. "
                "Ingest distributed trace telemetry before rebuilding topology."
            )

        snap_version = version or f"topo-v{datetime.now(UTC).strftime('%Y%m%d.%H%M%S')}"
        graph = build_topology_from_spans(
            spans=spans,
            time_window_start=st,
            time_window_end=et,
            version=snap_version,
            data_source=data_source,
        )

        snapshot_id = f"topo-{uuid.uuid4().hex[:12]}"

        snapshot_record = TopologySnapshotModel(
            id=snapshot_id,
            version=graph.version,
            data_source=graph.data_source,
            time_window_start=graph.time_window_start,
            time_window_end=graph.time_window_end,
            service_count=len(graph.nodes),
            edge_count=len(graph.edge_list),
            services_json=graph.service_names,
            metadata_json={
                "has_cycle": graph.has_cycle(),
                "diagnostics": graph.diagnostics,
            },
            created_at=datetime.now(UTC),
        )
        session.add(snapshot_record)
        session.flush()

        # Add edges
        for edge in graph.edge_list:
            edge_record = TopologyEdgeModel(
                topology_id=snapshot_id,
                caller_service=edge.caller_service,
                downstream_service=edge.downstream_service,
                call_count=edge.call_count,
                error_count=edge.error_count,
                error_rate=edge.error_rate,
                latency_p50_ms=edge.latency_p50_ms,
                latency_p95_ms=edge.latency_p95_ms,
                first_seen=edge.first_seen,
                last_seen=edge.last_seen,
                is_observed=edge.is_observed,
                is_manual=edge.is_manual,
                metadata_json=edge.metadata,
            )
            session.add(edge_record)

        session.commit()
        session.refresh(snapshot_record)
        return snapshot_record

    @staticmethod
    def get_latest_topology(session: Session) -> TopologySnapshotModel | None:
        """Retrieve the most recently created topology snapshot."""
        stmt = (
            select(TopologySnapshotModel).order_by(TopologySnapshotModel.created_at.desc()).limit(1)
        )
        return session.execute(stmt).scalars().first()

    @staticmethod
    def get_topology_by_id_or_version(
        session: Session, identifier: str
    ) -> TopologySnapshotModel | None:
        """Retrieve a topology snapshot by ID or semantic version string."""
        stmt = (
            select(TopologySnapshotModel)
            .where(
                (TopologySnapshotModel.id == identifier)
                | (TopologySnapshotModel.version == identifier)
            )
            .limit(1)
        )
        return session.execute(stmt).scalars().first()

    @staticmethod
    def list_snapshots(
        session: Session, limit: int = 20, offset: int = 0
    ) -> tuple[int, list[TopologySnapshotModel]]:
        """List historical topology snapshot records with total count."""
        count_stmt = select(func.count(TopologySnapshotModel.id))
        total = session.execute(count_stmt).scalar_one() or 0

        stmt = (
            select(TopologySnapshotModel)
            .order_by(TopologySnapshotModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(session.execute(stmt).scalars().all())
        return total, items

    @staticmethod
    def get_edges_for_snapshot(session: Session, topology_id: str) -> list[TopologyEdgeModel]:
        """Retrieve all directed dependency edges for a snapshot."""
        stmt = (
            select(TopologyEdgeModel)
            .where(TopologyEdgeModel.topology_id == topology_id)
            .order_by(
                TopologyEdgeModel.caller_service.asc(), TopologyEdgeModel.downstream_service.asc()
            )
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_service_dependencies(
        session: Session, service_name: str, topology_id: str | None = None
    ) -> dict[str, Any]:
        """Query direct callers and downstream dependencies for a given service."""
        if topology_id:
            snapshot = session.get(TopologySnapshotModel, topology_id)
        else:
            snapshot = TopologyService.get_latest_topology(session)

        if not snapshot:
            raise ValueError("No topology snapshot available. Rebuild topology first.")

        # Callers: caller -> service_name (downstream_service == service_name)
        caller_stmt = (
            select(TopologyEdgeModel)
            .where(
                TopologyEdgeModel.topology_id == snapshot.id,
                TopologyEdgeModel.downstream_service == service_name,
            )
            .order_by(TopologyEdgeModel.caller_service.asc())
        )
        caller_edges = list(session.execute(caller_stmt).scalars().all())

        # Downstream: service_name -> downstream (caller_service == service_name)
        downstream_stmt = (
            select(TopologyEdgeModel)
            .where(
                TopologyEdgeModel.topology_id == snapshot.id,
                TopologyEdgeModel.caller_service == service_name,
            )
            .order_by(TopologyEdgeModel.downstream_service.asc())
        )
        downstream_edges = list(session.execute(downstream_stmt).scalars().all())

        return {
            "service_name": service_name,
            "topology_id": snapshot.id,
            "topology_version": snapshot.version,
            "direct_callers": [
                {
                    "caller_service": e.caller_service,
                    "call_count": e.call_count,
                    "error_rate": e.error_rate,
                    "latency_p95_ms": e.latency_p95_ms,
                }
                for e in caller_edges
            ],
            "direct_downstream": [
                {
                    "downstream_service": e.downstream_service,
                    "call_count": e.call_count,
                    "error_rate": e.error_rate,
                    "latency_p95_ms": e.latency_p95_ms,
                }
                for e in downstream_edges
            ],
            "total_callers": len(caller_edges),
            "total_downstream": len(downstream_edges),
        }
