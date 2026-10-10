"""Deterministic root-cause ranking engine with explainable multi-factor scoring."""

from datetime import datetime
from typing import Any

from app.rca.schemas import (
    AnomalyEvent,
    CandidateEvidence,
    CorrelatedIncident,
    RankedCandidate,
)
from app.topology.models import TopologyGraph


class DeterministicRootCauseRanker:
    """Transparent, deterministic root-cause analysis baseline.

    Scores candidate services using:
    - Trace / call origin (leaf callee vs top-level caller)
    - Temporal anomaly onset precedence
    - Topological cascade reach
    - Anomaly confidence and severity
    """

    # Scoring weights sum to 1.0
    WEIGHT_ORIGIN = 0.35
    WEIGHT_TEMPORAL = 0.30
    WEIGHT_IMPACT = 0.20
    WEIGHT_SEVERITY = 0.15

    ABSTENTION_THRESHOLD = 0.20

    SEVERITY_WEIGHTS = {
        "CRITICAL": 1.0,
        "HIGH": 0.8,
        "MEDIUM": 0.5,
        "LOW": 0.3,
        "INFO": 0.1,
    }

    def rank_incident(
        self,
        incident: CorrelatedIncident,
        topology: TopologyGraph | None = None,
        trace_spans_summary: dict[str, dict[str, Any]] | None = None,
    ) -> list[RankedCandidate]:
        """Rank candidate services within a correlated incident deterministically."""
        if not incident.affected_services or not incident.events:
            return []

        # 1. Group events by service
        events_by_service: dict[str, list[AnomalyEvent]] = {}
        for ev in incident.events:
            events_by_service.setdefault(ev.service_name, []).append(ev)

        # 2. Determine global temporal bounds
        service_onsets: dict[str, datetime] = {}
        for svc, evs in events_by_service.items():
            valid_times = [e.timestamp for e in evs if e.timestamp is not None]
            if valid_times:
                service_onsets[svc] = min(valid_times)

        earliest_time = min(service_onsets.values()) if service_onsets else incident.start_time
        latest_time = max(service_onsets.values()) if service_onsets else incident.end_time
        max_temporal_span = max(0.0, (latest_time - earliest_time).total_seconds())

        candidates: list[RankedCandidate] = []
        affected_set = set(incident.affected_services)
        spans_summary = trace_spans_summary or {}

        for svc in incident.affected_services:
            svc_events = events_by_service.get(svc, [])
            onset = service_onsets.get(svc)

            # Sub-score 1: Temporal precedence
            temporal_score = 1.0
            relative_delay: float | None = None
            if onset and max_temporal_span > 0:
                relative_delay = max(0.0, (onset - earliest_time).total_seconds())
                temporal_score = max(0.0, 1.0 - (relative_delay / (max_temporal_span + 1.0)))
            elif onset:
                relative_delay = 0.0

            # Sub-score 2: Origin & Trace position
            # In caller -> downstream, an initiating failing callee has callers upstream among affected services.
            upstream_callers: list[str] = []
            downstream_symptoms: list[str] = []

            if topology:
                all_upstream = topology.get_all_upstream(svc)
                upstream_callers = sorted(
                    [u for u in all_upstream if u in affected_set and u != svc]
                )
                all_downstream = topology.get_all_downstream(svc)
                downstream_symptoms = sorted(
                    [d for d in all_downstream if d in affected_set and d != svc]
                )

            svc_span_data = spans_summary.get(svc, {})
            is_leaf_callee = bool(svc_span_data.get("is_leaf_error", False))
            failing_spans = int(svc_span_data.get("error_count", 0))

            # Callee origin score
            if is_leaf_callee:
                origin_score = 1.0
            elif upstream_callers and not downstream_symptoms:
                # Deepest callee in affected subgraph (e.g. payment called by order-service)
                origin_score = 0.90
            elif upstream_callers and downstream_symptoms:
                # Intermediate hop in cascade
                origin_score = 0.65
            elif not upstream_callers and downstream_symptoms:
                # Top-level entry caller (e.g. api-gateway or frontend initiating client)
                origin_score = 0.40
            else:
                # Isolated / unmapped service
                origin_score = 0.50

            # Sub-score 3: Topological cascade breadth
            # Measures how much of the affected cluster is topologically connected to this candidate
            if len(affected_set) <= 1:
                impact_score = 1.0
            else:
                connected_affected = set(upstream_callers) | set(downstream_symptoms)
                impact_score = len(connected_affected) / max(1, len(affected_set) - 1)
                impact_score = min(1.0, impact_score)

            # Sub-score 4: Severity & Anomaly Confidence
            scores = [e.anomaly_score for e in svc_events]
            mean_score = sum(scores) / len(scores) if scores else 0.0

            highest_sev = "LOW"
            highest_sev_val = 0.0
            for e in svc_events:
                val = self.SEVERITY_WEIGHTS.get(e.severity.upper(), 0.3)
                if val > highest_sev_val:
                    highest_sev_val = val
                    highest_sev = e.severity.upper()

            severity_score = (0.5 * mean_score) + (0.5 * highest_sev_val)

            # Total weighted heuristic score
            total_score = (
                self.WEIGHT_ORIGIN * origin_score
                + self.WEIGHT_TEMPORAL * temporal_score
                + self.WEIGHT_IMPACT * impact_score
                + self.WEIGHT_SEVERITY * severity_score
            )
            total_score = round(max(0.0, min(1.0, total_score)), 4)

            # Build transparent machine-readable evidence
            positive_indicators: list[str] = []
            contradictory_indicators: list[str] = []
            uncertainty_reasons: list[str] = []

            if relative_delay == 0.0:
                positive_indicators.append("Earliest anomaly onset in incident cluster")
            elif relative_delay is not None and relative_delay > 10.0:
                contradictory_indicators.append(
                    f"Anomaly onset lagged {relative_delay:.1f}s behind earliest incident signal"
                )

            if is_leaf_callee:
                positive_indicators.append(
                    "Identified as failing leaf callee in cross-service trace spans"
                )
            elif upstream_callers:
                positive_indicators.append(
                    f"Called by {len(upstream_callers)} affected upstream caller(s): {', '.join(upstream_callers[:3])}"
                )

            if downstream_symptoms:
                positive_indicators.append(
                    f"Has {len(downstream_symptoms)} downstream affected service(s): {', '.join(downstream_symptoms[:3])}"
                )

            if highest_sev in ("CRITICAL", "HIGH"):
                positive_indicators.append(
                    f"Elevated severity detected ({highest_sev}) with confidence {mean_score:.2f}"
                )

            if failing_spans > 0:
                positive_indicators.append(f"Observed {failing_spans} failing trace span(s)")

            if not topology or len(topology.nodes) == 0:
                uncertainty_reasons.append(
                    "No active topology snapshot available; structural evidence unverified"
                )
            elif svc not in topology.nodes:
                uncertainty_reasons.append("Service not present in current topology snapshot")

            if not svc_span_data:
                uncertainty_reasons.append(
                    "No distributed trace error spans linked to this service"
                )

            # Explanation summary
            summary_parts: list[str] = []
            if positive_indicators:
                summary_parts.append("; ".join(positive_indicators))
            if contradictory_indicators:
                summary_parts.append("Counter-evidence: " + "; ".join(contradictory_indicators))
            explanation_str = (
                " | ".join(summary_parts) if summary_parts else f"Candidate score {total_score:.2f}"
            )

            evidence = CandidateEvidence(
                candidate_service=svc,
                anomaly_onset=onset,
                relative_delay_sec=relative_delay,
                is_innermost_callee=is_leaf_callee,
                failing_span_count=failing_spans,
                downstream_symptoms=downstream_symptoms,
                upstream_callers=upstream_callers,
                anomaly_severity=highest_sev,
                mean_anomaly_score=round(mean_score, 4),
                detector_count=len({e.detector_name for e in svc_events}),
                positive_indicators=positive_indicators,
                contradictory_indicators=contradictory_indicators,
                uncertainty_reasons=uncertainty_reasons,
            )

            candidates.append(
                RankedCandidate(
                    service_name=svc,
                    rank=1,  # will be assigned post-sort
                    score=total_score,
                    score_breakdown={
                        "origin_score": round(origin_score, 4),
                        "temporal_score": round(temporal_score, 4),
                        "impact_score": round(impact_score, 4),
                        "severity_score": round(severity_score, 4),
                    },
                    evidence=evidence,
                    explanation=explanation_str,
                )
            )

        # Deterministic stable tie-breaking:
        # 1. Total score descending
        # 2. Onset timestamp ascending (earlier is better)
        # 3. Mean anomaly score descending
        # 4. Service name ascending (lexicographical)
        def sort_key(c: RankedCandidate) -> tuple[float, float, float, str]:
            score_inv = -c.score
            onset_ts = c.evidence.anomaly_onset.timestamp() if c.evidence.anomaly_onset else 1e12
            mean_score_inv = -c.evidence.mean_anomaly_score
            return (score_inv, onset_ts, mean_score_inv, c.service_name)

        candidates.sort(key=sort_key)

        # Assign 1-based ranks
        for idx, cand in enumerate(candidates, start=1):
            cand.rank = idx

        return candidates
