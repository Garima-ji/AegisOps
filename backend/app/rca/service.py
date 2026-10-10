"""Root cause analysis orchestration service for incident correlation, ranking, and persistence."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db.models.ml import AnomalyDetectionModel
from app.db.models.telemetry import TraceSpanModel
from app.db.models.topology import IncidentAnalysisModel, TopologySnapshotModel
from app.rca.correlator import AnomalyCorrelator
from app.rca.ranker import DeterministicRootCauseRanker
from app.rca.schemas import (
    AnomalyEvent,
    CorrelatedIncident,
    IncidentAnalysisListResponse,
    IncidentAnalysisSummary,
    RankedCandidate,
    RootCauseAnalysisRequest,
    RootCauseAnalysisResponse,
)
from app.topology.models import TopologyGraph, _to_utc
from app.topology.service import TopologyService

logger = logging.getLogger(__name__)


class RootCauseAnalysisService:
    """Service orchestrating incident anomaly correlation, graph topology analysis, and root-cause ranking."""

    def __init__(self) -> None:
        self.correlator = AnomalyCorrelator()
        self.ranker = DeterministicRootCauseRanker()
        self.topology_service = TopologyService()

    def analyze(
        self,
        request: RootCauseAnalysisRequest,
        db: Session,
    ) -> RootCauseAnalysisResponse:
        """Run root-cause analysis across the specified or inferred incident window."""
        now = datetime.now(UTC)
        time_end = _to_utc(request.time_window_end) if request.time_window_end else now
        time_start = (
            _to_utc(request.time_window_start)
            if request.time_window_start
            else (time_end - timedelta(minutes=request.lookback_minutes))
        )

        # 1. Fetch anomaly records from database
        events = self._fetch_anomaly_events(db, time_start, time_end)

        # 2. Fetch or load topology snapshot
        topology, topo_snapshot_id = self._resolve_topology(
            db, request.topology_id, time_start, time_end
        )

        # 3. Analyze distributed trace spans in window for error origin evidence
        trace_summary = self._analyze_trace_errors(db, time_start, time_end)

        # Diagnostics dict
        diagnostics: dict[str, Any] = {
            "query_window_start": time_start.isoformat(),
            "query_window_end": time_end.isoformat(),
            "anomalies_detected_count": len(events),
            "trace_error_services_count": len(trace_summary),
            "topology_node_count": len(topology.nodes) if topology else 0,
            "topology_edge_count": len(topology.edges) if topology else 0,
            "topology_snapshot_id": topo_snapshot_id,
        }

        # 4. Handle empty anomaly events -> Insufficient Evidence
        if not events:
            analysis_id = f"rca-{uuid.uuid4().hex[:12]}"
            response = RootCauseAnalysisResponse(
                analysis_id=analysis_id,
                incident_id=request.incident_id,
                status="INSUFFICIENT_EVIDENCE",
                time_window_start=time_start,
                time_window_end=time_end,
                topology_version=topo_snapshot_id,
                affected_services=[],
                top_candidate_service=None,
                confidence_score=None,
                summary_explanation="No operational anomalies detected within the specified observation window.",
                candidates=[],
                diagnostics=diagnostics,
                created_at=datetime.now(UTC),
            )
            if request.persist:
                self._persist_analysis(db, response)
            return response

        # 5. Correlate anomalies into incident clusters
        incidents = self.correlator.correlate(
            events=events,
            topology=topology,
            window_seconds=request.correlation_window_seconds,
            topology_version=topo_snapshot_id,
        )

        diagnostics["correlated_clusters_count"] = len(incidents)

        if not incidents:
            analysis_id = f"rca-{uuid.uuid4().hex[:12]}"
            response = RootCauseAnalysisResponse(
                analysis_id=analysis_id,
                incident_id=request.incident_id,
                status="INSUFFICIENT_EVIDENCE",
                time_window_start=time_start,
                time_window_end=time_end,
                topology_version=topo_snapshot_id,
                affected_services=[],
                top_candidate_service=None,
                confidence_score=None,
                summary_explanation="No correlated incident clusters formed from available anomalies.",
                candidates=[],
                diagnostics=diagnostics,
                created_at=datetime.now(UTC),
            )
            if request.persist:
                self._persist_analysis(db, response)
            return response

        # 6. Select relevant incident cluster
        target_incident = self._select_target_incident(incidents, request.incident_id)

        # 7. Run deterministic ranking baseline
        candidates = self.ranker.rank_incident(
            incident=target_incident,
            topology=topology,
            trace_spans_summary=trace_summary,
        )

        analysis_id = f"rca-{uuid.uuid4().hex[:12]}"
        status: Any = "COMPLETED"
        top_svc: str | None = None
        confidence: float | None = None
        summary_explanation: str = ""

        if not candidates or candidates[0].score < self.ranker.ABSTENTION_THRESHOLD:
            status = "INSUFFICIENT_EVIDENCE"
            top_svc = None
            confidence = None
            summary_explanation = (
                f"Evaluated {len(candidates)} candidate service(s), but highest candidate score did not meet "
                f"the minimum confidence threshold ({self.ranker.ABSTENTION_THRESHOLD})."
            )
        else:
            top_candidate = candidates[0]
            top_svc = top_candidate.service_name
            confidence = top_candidate.score
            summary_explanation = (
                f"Isolated '{top_svc}' as primary root-cause candidate with confidence {confidence:.2f} "
                f"across {len(target_incident.affected_services)} affected service(s). "
                f"{top_candidate.explanation}"
            )

        response = RootCauseAnalysisResponse(
            analysis_id=analysis_id,
            incident_id=target_incident.incident_id,
            status=status,
            time_window_start=target_incident.start_time,
            time_window_end=target_incident.end_time,
            topology_version=topo_snapshot_id,
            affected_services=target_incident.affected_services,
            top_candidate_service=top_svc,
            confidence_score=confidence,
            summary_explanation=summary_explanation,
            candidates=candidates,
            diagnostics=diagnostics,
            created_at=datetime.now(UTC),
        )

        if request.persist:
            self._persist_analysis(db, response)

        return response

    def get_analysis(
        self,
        analysis_id: str,
        db: Session,
    ) -> RootCauseAnalysisResponse | None:
        """Retrieve a previously persisted root-cause analysis run by ID."""
        stmt = select(IncidentAnalysisModel).where(IncidentAnalysisModel.id == analysis_id)
        record = db.execute(stmt).scalar_one_or_none()
        if not record:
            return None

        candidates = [RankedCandidate.model_validate(c) for c in record.candidates_json]

        return RootCauseAnalysisResponse(
            analysis_id=record.id,
            incident_id=record.incident_id,
            status=record.status,  # type: ignore[arg-type]
            time_window_start=record.time_window_start,
            time_window_end=record.time_window_end,
            topology_version=record.topology_version,
            affected_services=record.affected_services,
            top_candidate_service=record.top_candidate_service,
            confidence_score=record.confidence_score,
            summary_explanation=record.summary_explanation or "",
            candidates=candidates,
            diagnostics=record.diagnostics_json or {},
            created_at=record.created_at,
        )

    def list_analyses(
        self,
        limit: int,
        offset: int,
        db: Session,
    ) -> IncidentAnalysisListResponse:
        """List past incident analysis runs ordered by creation timestamp descending."""
        count_stmt = select(func.count(IncidentAnalysisModel.id))
        total = db.execute(count_stmt).scalar_one() or 0

        stmt = (
            select(IncidentAnalysisModel)
            .order_by(desc(IncidentAnalysisModel.created_at))
            .limit(limit)
            .offset(offset)
        )
        rows = db.execute(stmt).scalars().all()

        items = [
            IncidentAnalysisSummary(
                id=r.id,
                incident_id=r.incident_id,
                status=r.status,
                time_window_start=r.time_window_start,
                time_window_end=r.time_window_end,
                topology_version=r.topology_version,
                affected_services=r.affected_services,
                top_candidate_service=r.top_candidate_service,
                confidence_score=r.confidence_score,
                created_at=r.created_at,
            )
            for r in rows
        ]

        return IncidentAnalysisListResponse(
            total=total,
            items=items,
            limit=limit,
            offset=offset,
        )

    def _fetch_anomaly_events(
        self,
        db: Session,
        start_time: datetime,
        end_time: datetime,
    ) -> list[AnomalyEvent]:
        """Fetch anomaly records from the anomaly_detections table within time window."""
        stmt = (
            select(AnomalyDetectionModel)
            .where(
                AnomalyDetectionModel.timestamp >= start_time,
                AnomalyDetectionModel.timestamp <= end_time,
                AnomalyDetectionModel.is_anomaly.is_(True),
            )
            .order_by(AnomalyDetectionModel.timestamp.asc())
            .limit(5000)
        )
        records = db.execute(stmt).scalars().all()

        events: list[AnomalyEvent] = []
        for r in records:
            events.append(
                AnomalyEvent(
                    service_name=r.service_name,
                    timestamp=r.timestamp,
                    detector_name=r.detector_name,
                    anomaly_score=r.anomaly_score,
                    severity=r.severity,  # type: ignore[arg-type]
                    triggering_features=r.triggering_features or [],
                    explanation=r.explanation,
                    trace_id=r.trace_id,
                )
            )
        return events

    def _resolve_topology(
        self,
        db: Session,
        requested_topology_id: str | None,
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[TopologyGraph | None, str | None]:
        """Load requested topology snapshot, or latest active snapshot, or rebuild if empty."""
        snapshot_model: TopologySnapshotModel | None = None

        if requested_topology_id:
            stmt = select(TopologySnapshotModel).where(
                TopologySnapshotModel.id == requested_topology_id
            )
            snapshot_model = db.execute(stmt).scalar_one_or_none()
        else:
            snapshot_model = self.topology_service.get_latest_topology(db)

        if snapshot_model:
            # Reconstruct TopologyGraph from snapshot edges
            edge_models = self.topology_service.get_edges_for_snapshot(db, snapshot_model.id)
            graph = TopologyGraph()
            for s in snapshot_model.services_json or []:
                graph.add_service(s)
            for e in edge_models:
                graph.add_edge(
                    caller=e.caller_service,
                    downstream=e.downstream_service,
                    call_count=e.call_count,
                    error_count=e.error_count,
                    error_rate=e.error_rate,
                    latency_p50_ms=e.latency_p50_ms,
                    latency_p95_ms=e.latency_p95_ms,
                    is_observed=e.is_observed,
                    is_manual=e.is_manual,
                )
            return graph, snapshot_model.id

        # No snapshot exists in DB: attempt building on-the-fly across recent spans if any exist
        try:
            built_snapshot = self.topology_service.rebuild_topology(
                session=db,
                start_time=start_time - timedelta(hours=1),
                end_time=end_time,
            )
            return self._resolve_topology(db, built_snapshot.id, start_time, end_time)
        except Exception as exc:
            logger.warning("Could not auto-generate topology snapshot: %s", exc)
            return None, None

    def _analyze_trace_errors(
        self,
        db: Session,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, dict[str, Any]]:
        """Identify error trace spans and evaluate leaf failing callee relationships."""
        stmt = (
            select(TraceSpanModel)
            .where(
                TraceSpanModel.start_time >= start_time,
                TraceSpanModel.start_time <= end_time,
                TraceSpanModel.status_code == "ERROR",
            )
            .limit(2000)
        )
        error_spans = db.execute(stmt).scalars().all()

        if not error_spans:
            return {}

        # Group spans by trace_id to examine parent-child trees
        by_trace: dict[str, list[TraceSpanModel]] = {}
        for sp in error_spans:
            by_trace.setdefault(sp.trace_id, []).append(sp)

        service_stats: dict[str, dict[str, Any]] = {}

        for trace_id, spans in by_trace.items():
            parent_ids = {s.parent_span_id for s in spans if s.parent_span_id}

            for s in spans:
                stats = service_stats.setdefault(
                    s.service_name,
                    {"error_count": 0, "is_leaf_error": False, "traces": set()},
                )
                stats["error_count"] += 1
                stats["traces"].add(trace_id)

                # If this failing span's span_id is NOT a parent to any other failing span in this trace,
                # then it is a leaf error span!
                if s.span_id not in parent_ids:
                    stats["is_leaf_error"] = True

        return service_stats

    def _select_target_incident(
        self,
        incidents: list[CorrelatedIncident],
        incident_id: str | None,
    ) -> CorrelatedIncident:
        """Select requested incident or the cluster with largest affected impact."""
        if incident_id:
            for inc in incidents:
                if inc.incident_id == incident_id:
                    return inc

        # Default: select incident with most affected services, then most events
        return max(incidents, key=lambda i: (len(i.affected_services), len(i.events)))

    def _persist_analysis(
        self,
        db: Session,
        response: RootCauseAnalysisResponse,
    ) -> None:
        """Persist root-cause analysis run and candidate results to incident_analyses table."""
        model = IncidentAnalysisModel(
            id=response.analysis_id,
            incident_id=response.incident_id,
            status=response.status,
            time_window_start=response.time_window_start,
            time_window_end=response.time_window_end,
            topology_version=response.topology_version,
            affected_services=response.affected_services,
            top_candidate_service=response.top_candidate_service,
            confidence_score=response.confidence_score,
            summary_explanation=response.summary_explanation,
            candidates_json=[c.model_dump(mode="json") for c in response.candidates],
            diagnostics_json=response.diagnostics,
            created_at=response.created_at,
        )
        db.add(model)
        db.commit()
